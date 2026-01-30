"""Image classifier with face recognition, clustering, and duplicate detection."""

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import imagehash
import numpy as np
from PIL import Image

# Register HEIC/HEIF support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # pillow-heif not installed, HEIC files won't work

# Lazy imports for optional dependencies
_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_face_recognition_loaded = False

logger = logging.getLogger(__name__)


class Category(Enum):
    """Main classification categories."""
    KEEP = "keep"           # Family photos, pets, personal photos
    REVIEW = "review"       # Unknown faces, ambiguous photos
    TRASH = "trash"         # Screenshots, memes, graphics


class SubCategory(Enum):
    """Subcategories for organization within KEEP."""
    FAMILY = "family"           # Photos with known/recurring faces
    FAMILY_GROUP = "family_group"  # Multiple family members
    SELFIE = "selfie"           # Single family face, close-up
    PETS = "pets"               # Animals
    LANDSCAPES = "landscapes"   # Nature, travel
    FOOD = "food"               # Food photos
    EVENTS = "events"           # Celebrations, gatherings
    UNKNOWN_PEOPLE = "unknown_people"  # Faces not in family cluster
    OTHER = "other"             # Other real photos
    DUPLICATE = "duplicate"     # Duplicate of another image


@dataclass
class FaceInfo:
    """Information about a detected face."""
    embedding: np.ndarray
    location: tuple[int, int, int, int]  # top, right, bottom, left
    image_path: Path


@dataclass
class PersonCluster:
    """A cluster of faces belonging to the same person."""
    id: int
    face_count: int
    sample_paths: list[Path]
    is_family: bool = False  # True if appears frequently


@dataclass
class DuplicateGroup:
    """Group of duplicate images."""
    id: int
    images: list[Path]
    best_image: Path  # The one to keep
    duplicates: list[Path]  # The ones to mark as duplicates


@dataclass
class ClassificationResult:
    """Result of classifying a single image."""
    path: Path
    category: Category
    subcategory: SubCategory | None
    has_faces: bool
    face_count: int
    family_face_count: int
    unknown_face_count: int
    has_pets: bool
    clip_scores: dict[str, float]
    best_clip_label: str
    confidence: float
    person_ids: list[int] = field(default_factory=list)  # IDs of detected people
    is_duplicate: bool = False
    duplicate_of: Path | None = None  # Path to the "best" image in duplicate group
    duplicate_group_id: int | None = None
    error: str | None = None


@dataclass
class ClassificationReport:
    """Full classification report."""
    total_images: int
    keep_count: int
    review_count: int
    trash_count: int
    duplicate_count: int
    results: list[ClassificationResult]
    errors: list[tuple[Path, str]]
    person_clusters: list[PersonCluster] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    family_threshold: int = 5  # Minimum appearances to be considered family
    potential_savings: int = 0  # Bytes that can be saved by removing duplicates


# Improved CLIP labels
PHOTO_LABELS = [
    "a photograph of people",
    "a family photo",
    "a portrait photograph",
    "a selfie",
    "a group photo of people",
    "a photograph of a child",
    "a photograph of a baby",
    "a photograph of a dog",
    "a photograph of a cat",
    "a photograph of a pet",
    "a photograph of an animal",
    "a photograph of a landscape",
    "a photograph of nature",
    "a photograph of food",
    "a photograph of a meal",
    "a photograph of an event or celebration",
    "a travel photograph",
    "a vacation photo",
    "a beach photograph",
    "a photograph of a house or building",
]

JUNK_LABELS = [
    "a screenshot of a phone",
    "a screenshot of a computer screen",
    "a screenshot of a chat or message",
    "a meme or internet joke",
    "a diagram or infographic",
    "a chart or graph",
    "digital art or illustration",
    "a logo or brand image",
    "text on a plain background",
    "a wallpaper or background image",
    "a product image from a website",
    "an advertisement",
    "a document or form",
    "a QR code",
    "a receipt or ticket",
]

PET_LABELS = [
    "a photograph of a dog",
    "a photograph of a cat",
    "a photograph of a pet",
    "a photograph of an animal",
]

ALL_LABELS = PHOTO_LABELS + JUNK_LABELS


def _load_clip():
    """Lazy load CLIP model."""
    global _clip_model, _clip_preprocess, _clip_tokenizer

    if _clip_model is not None:
        return _clip_model, _clip_preprocess, _clip_tokenizer

    try:
        import torch
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32',
            pretrained='laion2b_s34b_b79k'
        )
        tokenizer = open_clip.get_tokenizer('ViT-B-32')

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device)
        model.eval()

        _clip_model = model
        _clip_preprocess = preprocess
        _clip_tokenizer = tokenizer

        return model, preprocess, tokenizer
    except ImportError:
        raise ImportError(
            "CLIP dependencies not installed. Run: pip install image-dedup[classify]"
        )


def _ensure_face_recognition():
    """Ensure face_recognition is loaded."""
    global _face_recognition_loaded
    if not _face_recognition_loaded:
        try:
            import face_recognition
            _face_recognition_loaded = True
        except ImportError:
            raise ImportError(
                "face_recognition not installed. Run: pip install image-dedup[classify]"
            )


def extract_face_embeddings(image_path: Path) -> list[tuple[np.ndarray, tuple]]:
    """
    Extract face embeddings from an image using face_recognition.

    Returns:
        List of (embedding, location) tuples for each detected face
    """
    _ensure_face_recognition()
    import face_recognition

    try:
        # Load image
        image = face_recognition.load_image_file(str(image_path))

        # Find faces and get embeddings
        face_locations = face_recognition.face_locations(image, model="hog")

        if not face_locations:
            return []

        face_encodings = face_recognition.face_encodings(image, face_locations)

        return list(zip(face_encodings, face_locations))
    except Exception as e:
        logger.debug(f"Error extracting faces from {image_path}: {e}")
        return []


def cluster_faces(
    all_faces: list[FaceInfo],
    tolerance: float = 0.6
) -> list[PersonCluster]:
    """
    Cluster faces into groups (same person).

    Uses a simple greedy clustering approach based on face_recognition distance.
    """
    _ensure_face_recognition()
    import face_recognition

    if not all_faces:
        return []

    # Simple greedy clustering
    clusters: list[list[FaceInfo]] = []

    for face in all_faces:
        matched_cluster = None

        for cluster in clusters:
            # Compare with first face in cluster (representative)
            representative = cluster[0]
            distance = face_recognition.face_distance(
                [representative.embedding],
                face.embedding
            )[0]

            if distance < tolerance:
                matched_cluster = cluster
                break

        if matched_cluster is not None:
            matched_cluster.append(face)
        else:
            clusters.append([face])

    # Convert to PersonCluster objects
    person_clusters = []
    for i, cluster in enumerate(clusters):
        # Get unique image paths
        unique_paths = list(set(f.image_path for f in cluster))
        person_clusters.append(PersonCluster(
            id=i,
            face_count=len(cluster),
            sample_paths=unique_paths[:5],  # Keep up to 5 sample paths
            is_family=False  # Will be set later based on frequency
        ))

    return person_clusters


def identify_family_members(
    clusters: list[PersonCluster],
    min_appearances: int = 5
) -> list[PersonCluster]:
    """
    Identify which clusters represent family members.

    Family members are people who appear in many photos.
    """
    for cluster in clusters:
        # Count unique images this person appears in
        unique_images = len(cluster.sample_paths)
        cluster.is_family = unique_images >= min_appearances

    return clusters


def classify_with_clip(image_path: Path) -> tuple[dict[str, float], str, float]:
    """
    Classify an image using CLIP.

    Returns:
        Tuple of (scores_dict, best_label, confidence)
    """
    import torch

    model, preprocess, tokenizer = _load_clip()
    device = next(model.parameters()).device

    with Image.open(image_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        image_tensor = preprocess(img).unsqueeze(0).to(device)

    text_tokens = tokenizer(ALL_LABELS).to(device)

    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
        text_features = model.encode_text(text_tokens)

        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
        scores = similarity[0].cpu().numpy()

    scores_dict = {label: float(score) for label, score in zip(ALL_LABELS, scores)}

    best_idx = scores.argmax()
    best_label = ALL_LABELS[best_idx]
    confidence = float(scores[best_idx])

    return scores_dict, best_label, confidence


def has_pet_in_image(clip_scores: dict[str, float], threshold: float = 0.15) -> bool:
    """Check if the image likely contains a pet/animal."""
    pet_score = sum(clip_scores.get(label, 0) for label in PET_LABELS)
    return pet_score > threshold


def determine_subcategory(
    has_faces: bool,
    family_face_count: int,
    unknown_face_count: int,
    has_pets: bool,
    clip_scores: dict[str, float],
    best_label: str,
) -> SubCategory:
    """Determine the subcategory for organization."""

    # Family photos
    if family_face_count > 1:
        return SubCategory.FAMILY_GROUP
    elif family_face_count == 1 and unknown_face_count == 0:
        # Check if it's a selfie (close-up single face)
        if "selfie" in best_label.lower() or "portrait" in best_label.lower():
            return SubCategory.SELFIE
        return SubCategory.FAMILY

    # Unknown people
    if unknown_face_count > 0:
        return SubCategory.UNKNOWN_PEOPLE

    # Pets
    if has_pets:
        return SubCategory.PETS

    # Other categories based on CLIP
    if "landscape" in best_label.lower() or "nature" in best_label.lower() or "travel" in best_label.lower() or "beach" in best_label.lower() or "vacation" in best_label.lower():
        return SubCategory.LANDSCAPES

    if "food" in best_label.lower() or "meal" in best_label.lower():
        return SubCategory.FOOD

    if "event" in best_label.lower() or "celebration" in best_label.lower():
        return SubCategory.EVENTS

    return SubCategory.OTHER


def compute_image_hashes(image_path: Path) -> tuple[str | None, str | None]:
    """
    Compute perceptual hashes for duplicate detection.

    Returns:
        Tuple of (phash, dhash) as hex strings, or None if error
    """
    try:
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            phash = str(imagehash.phash(img, hash_size=16))
            dhash = str(imagehash.dhash(img, hash_size=16))
            return phash, dhash
    except Exception as e:
        logger.debug(f"Error computing hash for {image_path}: {e}")
        return None, None


def find_duplicates(
    image_paths: list[Path],
    image_scores: dict[Path, float],  # Score for each image (higher = better to keep)
    similarity_threshold: int = 10,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """
    Find duplicate and similar images using perceptual hashing.

    Args:
        image_paths: List of image paths
        image_scores: Score for each image (higher = better)
        similarity_threshold: Max hamming distance to consider similar

    Returns:
        List of DuplicateGroup with best image identified
    """
    # Compute hashes
    hashes: dict[Path, tuple[str | None, str | None]] = {}
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if progress_callback and i % 50 == 0:
            progress_callback(f"Computing hashes: {path.name}", i, total)
        hashes[path] = compute_image_hashes(path)

    # Group by exact hash match first
    phash_groups: dict[str, list[Path]] = defaultdict(list)
    for path, (phash, dhash) in hashes.items():
        if phash:
            phash_groups[phash].append(path)

    # Find duplicates
    duplicate_groups: list[DuplicateGroup] = []
    processed: set[Path] = set()
    group_id = 0

    # Exact duplicates (same phash)
    for phash, paths in phash_groups.items():
        if len(paths) > 1:
            # Sort by score (highest first)
            sorted_paths = sorted(paths, key=lambda p: image_scores.get(p, 0), reverse=True)
            best = sorted_paths[0]
            dups = sorted_paths[1:]

            duplicate_groups.append(DuplicateGroup(
                id=group_id,
                images=paths,
                best_image=best,
                duplicates=dups,
            ))
            group_id += 1
            processed.update(paths)

    # Similar images (close phash)
    remaining = [p for p in image_paths if p not in processed and hashes[p][0]]

    for i, path1 in enumerate(remaining):
        if path1 in processed:
            continue

        phash1 = hashes[path1][0]
        if not phash1:
            continue

        similar_group = [path1]

        for path2 in remaining[i + 1:]:
            if path2 in processed:
                continue

            phash2 = hashes[path2][0]
            if not phash2:
                continue

            # Compute hamming distance
            try:
                h1 = imagehash.hex_to_hash(phash1)
                h2 = imagehash.hex_to_hash(phash2)
                distance = h1 - h2

                if distance <= similarity_threshold:
                    similar_group.append(path2)
            except Exception:
                continue

        if len(similar_group) > 1:
            # Sort by score
            sorted_paths = sorted(similar_group, key=lambda p: image_scores.get(p, 0), reverse=True)
            best = sorted_paths[0]
            dups = sorted_paths[1:]

            duplicate_groups.append(DuplicateGroup(
                id=group_id,
                images=similar_group,
                best_image=best,
                duplicates=dups,
            ))
            group_id += 1
            processed.update(similar_group)

    return duplicate_groups


def classify_images(
    image_paths: list[Path],
    progress_callback: Callable[[str, int, int], None] | None = None,
    family_threshold: int = 5,
    face_tolerance: float = 0.6,
    duplicate_threshold: int = 10,
    find_duplicates_flag: bool = True,
) -> ClassificationReport:
    """
    Classify images with face recognition, clustering, and duplicate detection.

    Args:
        image_paths: List of image paths to classify
        progress_callback: Optional callback for progress updates
        family_threshold: Minimum appearances to be considered family
        face_tolerance: Face matching tolerance (lower = stricter)
        duplicate_threshold: Max hamming distance to consider similar (0-64)
        find_duplicates_flag: Whether to find duplicates

    Returns:
        ClassificationReport with results, face clusters, and duplicate info
    """
    total = len(image_paths)

    # Phase 1: Extract all face embeddings
    if progress_callback:
        progress_callback("Phase 1: Extracting faces...", 0, total)

    all_faces: list[FaceInfo] = []
    image_faces: dict[Path, list[tuple[np.ndarray, tuple]]] = {}

    for i, path in enumerate(image_paths):
        if progress_callback and i % 10 == 0:
            progress_callback(f"Extracting faces: {path.name}", i, total)

        try:
            faces = extract_face_embeddings(path)
            image_faces[path] = faces

            for embedding, location in faces:
                all_faces.append(FaceInfo(
                    embedding=embedding,
                    location=location,
                    image_path=path
                ))
        except Exception as e:
            logger.debug(f"Error processing {path}: {e}")
            image_faces[path] = []

    # Phase 2: Cluster faces
    if progress_callback:
        progress_callback("Phase 2: Clustering faces...", total // 2, total)

    person_clusters = cluster_faces(all_faces, tolerance=face_tolerance)
    person_clusters = identify_family_members(person_clusters, min_appearances=family_threshold)

    # Create lookup: embedding -> person_id
    family_person_ids = {c.id for c in person_clusters if c.is_family}

    logger.info(f"Found {len(person_clusters)} unique people, {len(family_person_ids)} identified as family")

    # Phase 3: Classify each image
    if progress_callback:
        progress_callback("Phase 3: Classifying images...", total // 2, total)

    results = []
    errors = []

    # Pre-compute face to cluster mapping
    def find_person_id(embedding: np.ndarray) -> int | None:
        """Find which cluster a face belongs to."""
        _ensure_face_recognition()
        import face_recognition

        for cluster in person_clusters:
            if not cluster.sample_paths:
                continue
            # Get first face from cluster for comparison
            for face in all_faces:
                if face.image_path in cluster.sample_paths:
                    distance = face_recognition.face_distance([face.embedding], embedding)[0]
                    if distance < face_tolerance:
                        return cluster.id
                    break
        return None

    for i, path in enumerate(image_paths):
        if progress_callback and i % 10 == 0:
            progress_callback(f"Classifying: {path.name}", total // 2 + i // 2, total)

        try:
            # Get face info for this image
            faces = image_faces.get(path, [])
            has_faces = len(faces) > 0
            face_count = len(faces)

            # Count family vs unknown faces
            person_ids = []
            family_face_count = 0
            unknown_face_count = 0

            for embedding, _ in faces:
                pid = find_person_id(embedding)
                if pid is not None:
                    person_ids.append(pid)
                    if pid in family_person_ids:
                        family_face_count += 1
                    else:
                        unknown_face_count += 1
                else:
                    unknown_face_count += 1

            # Get CLIP classification
            clip_scores, best_label, confidence = classify_with_clip(path)

            # Check for pets
            has_pets = has_pet_in_image(clip_scores)

            # Determine category
            photo_score = sum(clip_scores.get(l, 0) for l in PHOTO_LABELS)
            junk_score = sum(clip_scores.get(l, 0) for l in JUNK_LABELS)

            # Classification logic
            if family_face_count > 0:
                # Has family faces - definitely KEEP
                category = Category.KEEP
            elif has_pets:
                # Has pets - KEEP
                category = Category.KEEP
            elif unknown_face_count > 0:
                # Has faces but not family - REVIEW (might be WhatsApp)
                category = Category.REVIEW
            elif photo_score > junk_score and photo_score > 0.25:
                # Looks like a real photo - KEEP or REVIEW
                # Be more generous - keep real photos
                category = Category.KEEP
            else:
                # Probably junk
                category = Category.TRASH

            # Determine subcategory
            subcategory = None
            if category == Category.KEEP:
                subcategory = determine_subcategory(
                    has_faces, family_face_count, unknown_face_count,
                    has_pets, clip_scores, best_label
                )

            results.append(ClassificationResult(
                path=path,
                category=category,
                subcategory=subcategory,
                has_faces=has_faces,
                face_count=face_count,
                family_face_count=family_face_count,
                unknown_face_count=unknown_face_count,
                has_pets=has_pets,
                clip_scores=clip_scores,
                best_clip_label=best_label,
                confidence=confidence,
                person_ids=person_ids,
            ))

        except Exception as e:
            logger.debug(f"Error classifying {path}: {e}")
            errors.append((path, str(e)))
            results.append(ClassificationResult(
                path=path,
                category=Category.REVIEW,
                subcategory=None,
                has_faces=False,
                face_count=0,
                family_face_count=0,
                unknown_face_count=0,
                has_pets=False,
                clip_scores={},
                best_clip_label="error",
                confidence=0.0,
                error=str(e),
            ))

    # Phase 4: Find duplicates
    duplicate_groups = []
    duplicate_count = 0
    potential_savings = 0

    if find_duplicates_flag:
        if progress_callback:
            progress_callback("Phase 4: Finding duplicates...", total * 3 // 4, total)

        # Compute score for each image (higher = better to keep)
        # Score based on: family faces > any faces > pets > real photo > junk
        image_scores: dict[Path, float] = {}
        for r in results:
            score = 0.0
            # Family faces are most important
            score += r.family_face_count * 100
            # Any faces
            score += r.face_count * 10
            # Pets
            if r.has_pets:
                score += 5
            # Keep category is better than others
            if r.category == Category.KEEP:
                score += 3
            elif r.category == Category.REVIEW:
                score += 1
            # File size as tiebreaker (larger = better quality)
            try:
                score += r.path.stat().st_size / 1_000_000  # MB
            except Exception:
                pass
            image_scores[r.path] = score

        duplicate_groups = find_duplicates(
            image_paths,
            image_scores,
            similarity_threshold=duplicate_threshold,
            progress_callback=progress_callback,
        )

        # Mark duplicates in results
        duplicate_map: dict[Path, tuple[Path, int]] = {}  # path -> (best_path, group_id)
        for group in duplicate_groups:
            for dup_path in group.duplicates:
                duplicate_map[dup_path] = (group.best_image, group.id)

        for r in results:
            if r.path in duplicate_map:
                best_path, group_id = duplicate_map[r.path]
                r.is_duplicate = True
                r.duplicate_of = best_path
                r.duplicate_group_id = group_id
                # Change subcategory to DUPLICATE for organization
                if r.category == Category.KEEP:
                    r.subcategory = SubCategory.DUPLICATE
                duplicate_count += 1
                try:
                    potential_savings += r.path.stat().st_size
                except Exception:
                    pass

        logger.info(f"Found {len(duplicate_groups)} duplicate groups, {duplicate_count} duplicate images")

    if progress_callback:
        progress_callback("Done", total, total)

    # Count categories (after duplicate marking)
    keep_count = sum(1 for r in results if r.category == Category.KEEP and not r.is_duplicate)
    review_count = sum(1 for r in results if r.category == Category.REVIEW)
    trash_count = sum(1 for r in results if r.category == Category.TRASH)

    return ClassificationReport(
        total_images=total,
        keep_count=keep_count,
        review_count=review_count,
        trash_count=trash_count,
        duplicate_count=duplicate_count,
        results=results,
        errors=errors,
        person_clusters=person_clusters,
        duplicate_groups=duplicate_groups,
        family_threshold=family_threshold,
        potential_savings=potential_savings,
    )
