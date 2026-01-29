"""Image classifier to separate real photos from junk."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from PIL import Image

# Lazy imports for optional dependencies
_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_face_detector = None


class Category(Enum):
    """Classification categories."""
    KEEP = "keep"           # Photos with faces - definitely keep
    REVIEW = "review"       # Real photos without faces - review manually
    TRASH = "trash"         # Screenshots, memes, graphics - probably delete


@dataclass
class ClassificationResult:
    """Result of classifying a single image."""
    path: Path
    category: Category
    has_faces: bool
    face_count: int
    clip_scores: dict[str, float]
    best_clip_label: str
    confidence: float
    error: str | None = None


@dataclass
class ClassificationReport:
    """Full classification report."""
    total_images: int
    keep_count: int
    review_count: int
    trash_count: int
    results: list[ClassificationResult]
    errors: list[tuple[Path, str]]


# CLIP labels for classification
PHOTO_LABELS = [
    "a photograph of people",
    "a family photo",
    "a portrait photograph",
    "a selfie",
    "a group photo",
    "a photograph of a landscape",
    "a photograph of nature",
    "a photograph of food",
    "a photograph of an event",
    "a travel photograph",
]

JUNK_LABELS = [
    "a screenshot of a phone",
    "a screenshot of a computer",
    "a meme",
    "a diagram",
    "a chart or graph",
    "digital art or illustration",
    "a logo",
    "text on a plain background",
    "a wallpaper or background image",
    "a product image from a website",
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

        # Use a smaller, faster model
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32',
            pretrained='laion2b_s34b_b79k'
        )
        tokenizer = open_clip.get_tokenizer('ViT-B-32')

        # Move to GPU if available
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


def _load_face_detector():
    """Lazy load face detector."""
    global _face_detector

    if _face_detector is not None:
        return _face_detector

    try:
        import mediapipe as mp

        _face_detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1,  # Full range model
            min_detection_confidence=0.5
        )
        return _face_detector
    except ImportError:
        raise ImportError(
            "MediaPipe not installed. Run: pip install image-dedup[classify]"
        )


def detect_faces(image_path: Path) -> tuple[bool, int]:
    """
    Detect faces in an image.

    Returns:
        Tuple of (has_faces, face_count)
    """
    import numpy as np

    detector = _load_face_detector()

    with Image.open(image_path) as img:
        # Convert to RGB if needed
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Convert to numpy array
        img_array = np.array(img)

        # Detect faces
        results = detector.process(img_array)

        if results.detections:
            return True, len(results.detections)
        return False, 0


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
        # Convert to RGB if needed
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Preprocess image
        image_tensor = preprocess(img).unsqueeze(0).to(device)

    # Tokenize labels
    text_tokens = tokenizer(ALL_LABELS).to(device)

    # Get predictions
    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
        text_features = model.encode_text(text_tokens)

        # Normalize
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        # Calculate similarity
        similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
        scores = similarity[0].cpu().numpy()

    # Build scores dict
    scores_dict = {label: float(score) for label, score in zip(ALL_LABELS, scores)}

    # Find best match
    best_idx = scores.argmax()
    best_label = ALL_LABELS[best_idx]
    confidence = float(scores[best_idx])

    return scores_dict, best_label, confidence


def classify_image(image_path: Path) -> ClassificationResult:
    """
    Classify a single image.

    Returns:
        ClassificationResult with category and details
    """
    try:
        # Detect faces
        has_faces, face_count = detect_faces(image_path)

        # Classify with CLIP
        clip_scores, best_label, confidence = classify_with_clip(image_path)

        # Determine category
        is_photo_label = best_label in PHOTO_LABELS

        # Calculate aggregated scores
        photo_score = sum(clip_scores.get(l, 0) for l in PHOTO_LABELS)
        junk_score = sum(clip_scores.get(l, 0) for l in JUNK_LABELS)

        if has_faces:
            # Photos with faces are always KEEP
            category = Category.KEEP
        elif photo_score > junk_score and photo_score > 0.3:
            # Looks like a real photo but no faces - review
            category = Category.REVIEW
        else:
            # Probably junk
            category = Category.TRASH

        return ClassificationResult(
            path=image_path,
            category=category,
            has_faces=has_faces,
            face_count=face_count,
            clip_scores=clip_scores,
            best_clip_label=best_label,
            confidence=confidence,
        )

    except Exception as e:
        return ClassificationResult(
            path=image_path,
            category=Category.REVIEW,  # When in doubt, review
            has_faces=False,
            face_count=0,
            clip_scores={},
            best_clip_label="error",
            confidence=0.0,
            error=str(e),
        )


def classify_images(
    image_paths: list[Path],
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> ClassificationReport:
    """
    Classify multiple images.

    Args:
        image_paths: List of image paths to classify
        progress_callback: Optional callback for progress updates

    Returns:
        ClassificationReport with all results
    """
    results = []
    errors = []

    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if progress_callback:
            progress_callback(f"Classifying {path.name}", i, total)

        result = classify_image(path)
        results.append(result)

        if result.error:
            errors.append((path, result.error))

    if progress_callback:
        progress_callback("Done", total, total)

    # Count categories
    keep_count = sum(1 for r in results if r.category == Category.KEEP)
    review_count = sum(1 for r in results if r.category == Category.REVIEW)
    trash_count = sum(1 for r in results if r.category == Category.TRASH)

    return ClassificationReport(
        total_images=total,
        keep_count=keep_count,
        review_count=review_count,
        trash_count=trash_count,
        results=results,
        errors=errors,
    )
