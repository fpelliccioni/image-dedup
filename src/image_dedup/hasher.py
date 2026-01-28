"""Hash computation for images (exact and perceptual)."""

import hashlib
from pathlib import Path

import imagehash
from PIL import Image


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file (exact duplicate detection)."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_phash(file_path: Path, hash_size: int = 16) -> imagehash.ImageHash:
    """
    Compute perceptual hash of an image (similar image detection).

    Uses pHash algorithm which is robust against:
    - Resizing
    - Compression artifacts
    - Minor color adjustments

    Args:
        file_path: Path to the image file
        hash_size: Size of the hash (larger = more precise, default 16)

    Returns:
        ImageHash object that can be compared with other hashes
    """
    with Image.open(file_path) as img:
        return imagehash.phash(img, hash_size=hash_size)


def compute_dhash(file_path: Path, hash_size: int = 16) -> imagehash.ImageHash:
    """
    Compute difference hash of an image.

    Faster than pHash but slightly less accurate.
    Good for detecting crops and minor edits.
    """
    with Image.open(file_path) as img:
        return imagehash.dhash(img, hash_size=hash_size)


def compute_ahash(file_path: Path, hash_size: int = 16) -> imagehash.ImageHash:
    """
    Compute average hash of an image.

    Fastest but least accurate. Good for quick filtering.
    """
    with Image.open(file_path) as img:
        return imagehash.average_hash(img, hash_size=hash_size)


def hash_distance(hash1: imagehash.ImageHash, hash2: imagehash.ImageHash) -> int:
    """
    Compute Hamming distance between two perceptual hashes.

    Returns:
        Number of different bits. Lower = more similar.
        - 0: Identical
        - 1-10: Very similar (likely same image, different compression)
        - 11-20: Somewhat similar
        - >20: Different images
    """
    return hash1 - hash2
