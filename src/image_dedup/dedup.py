"""Main deduplication logic."""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import imagehash

from .hasher import compute_dhash, compute_phash, compute_sha256
from .scanner import scan_multiple_directories


@dataclass
class ImageInfo:
    """Information about an image file."""
    path: Path
    size: int
    sha256: str | None = None
    phash: imagehash.ImageHash | None = None
    dhash: imagehash.ImageHash | None = None


@dataclass
class DuplicateGroup:
    """A group of duplicate or similar images."""
    images: list[ImageInfo] = field(default_factory=list)
    match_type: str = "exact"  # "exact" or "similar"
    similarity: int | None = None  # Hamming distance for similar matches

    @property
    def total_size(self) -> int:
        """Total size of all images in the group."""
        return sum(img.size for img in self.images)

    @property
    def potential_savings(self) -> int:
        """Space that could be saved by keeping only one image."""
        if len(self.images) <= 1:
            return 0
        # Keep the largest (usually best quality), remove rest
        sizes = sorted((img.size for img in self.images), reverse=True)
        return sum(sizes[1:])


@dataclass
class DeduplicationResult:
    """Results of a deduplication scan."""
    exact_duplicates: list[DuplicateGroup] = field(default_factory=list)
    similar_images: list[DuplicateGroup] = field(default_factory=list)
    total_images: int = 0
    total_size: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def exact_duplicate_count(self) -> int:
        """Number of exact duplicate images (excluding originals)."""
        return sum(len(g.images) - 1 for g in self.exact_duplicates)

    @property
    def similar_count(self) -> int:
        """Number of similar image groups."""
        return len(self.similar_images)

    @property
    def potential_savings_exact(self) -> int:
        """Potential space savings from exact duplicates."""
        return sum(g.potential_savings for g in self.exact_duplicates)

    @property
    def potential_savings_similar(self) -> int:
        """Potential space savings from similar images."""
        return sum(g.potential_savings for g in self.similar_images)


def find_duplicates(
    directories: list[Path],
    recursive: bool = True,
    find_exact: bool = True,
    find_similar: bool = True,
    similarity_threshold: int = 10,
    hash_size: int = 16,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> DeduplicationResult:
    """
    Find duplicate and similar images in the given directories.

    Args:
        directories: List of directories to scan
        recursive: Whether to scan subdirectories
        find_exact: Whether to find exact duplicates (SHA256)
        find_similar: Whether to find similar images (perceptual hash)
        similarity_threshold: Maximum Hamming distance for similar images (0-64)
        hash_size: Size of perceptual hash (larger = more precise)
        progress_callback: Optional callback(status, current, total) for progress updates

    Returns:
        DeduplicationResult with all findings
    """
    result = DeduplicationResult()

    # Phase 1: Scan and collect all images
    if progress_callback:
        progress_callback("Scanning directories...", 0, 0)

    images: list[ImageInfo] = []
    for path in scan_multiple_directories(directories, recursive):
        try:
            size = path.stat().st_size
            images.append(ImageInfo(path=path, size=size))
            result.total_size += size
        except OSError as e:
            result.errors.append((path, str(e)))

    result.total_images = len(images)

    if not images:
        return result

    # Phase 2: Compute hashes
    sha256_groups: dict[str, list[ImageInfo]] = defaultdict(list)
    phash_list: list[ImageInfo] = []

    for i, img in enumerate(images):
        if progress_callback:
            progress_callback("Computing hashes...", i + 1, len(images))

        try:
            if find_exact:
                img.sha256 = compute_sha256(img.path)
                sha256_groups[img.sha256].append(img)

            if find_similar:
                img.phash = compute_phash(img.path, hash_size)
                img.dhash = compute_dhash(img.path, hash_size)
                phash_list.append(img)

        except Exception as e:
            result.errors.append((img.path, str(e)))

    # Phase 3: Find exact duplicates
    if find_exact:
        for sha256, group in sha256_groups.items():
            if len(group) > 1:
                result.exact_duplicates.append(
                    DuplicateGroup(images=group, match_type="exact")
                )

    # Phase 4: Find similar images (excluding exact duplicates)
    if find_similar:
        if progress_callback:
            progress_callback("Finding similar images...", 0, len(phash_list))

        # Set of SHA256 hashes that are exact duplicates
        exact_hashes = {img.sha256 for g in result.exact_duplicates for img in g.images}

        # Filter out images that are exact duplicates (keep only one per group)
        seen_sha256: set[str] = set()
        unique_images: list[ImageInfo] = []
        for img in phash_list:
            if img.sha256 not in seen_sha256:
                seen_sha256.add(img.sha256)
                unique_images.append(img)

        # Find similar images using perceptual hash
        matched: set[int] = set()

        for i, img1 in enumerate(unique_images):
            if i in matched:
                continue

            if progress_callback:
                progress_callback("Finding similar images...", i + 1, len(unique_images))

            similar_group: list[ImageInfo] = [img1]
            min_distance = float("inf")

            for j, img2 in enumerate(unique_images[i + 1:], start=i + 1):
                if j in matched:
                    continue

                # Use both pHash and dHash for better accuracy
                phash_dist = img1.phash - img2.phash
                dhash_dist = img1.dhash - img2.dhash

                # Average of both hashes
                avg_dist = (phash_dist + dhash_dist) / 2

                if avg_dist <= similarity_threshold:
                    similar_group.append(img2)
                    matched.add(j)
                    min_distance = min(min_distance, avg_dist)

            if len(similar_group) > 1:
                matched.add(i)
                result.similar_images.append(
                    DuplicateGroup(
                        images=similar_group,
                        match_type="similar",
                        similarity=int(min_distance)
                    )
                )

    return result


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
