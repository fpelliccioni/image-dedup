"""Directory scanner for finding image files."""

from pathlib import Path
from typing import Iterator

# Common image extensions
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw",
    ".dng", ".orf", ".rw2", ".pef", ".sr2"
}


def is_image_file(path: Path) -> bool:
    """Check if a file is an image based on extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def scan_directory(
    directory: Path,
    recursive: bool = True,
    extensions: set[str] | None = None
) -> Iterator[Path]:
    """
    Scan a directory for image files.

    Args:
        directory: Directory to scan
        recursive: Whether to scan subdirectories
        extensions: Custom set of extensions to look for (default: IMAGE_EXTENSIONS)

    Yields:
        Path objects for each image file found
    """
    if extensions is None:
        extensions = IMAGE_EXTENSIONS

    extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}

    pattern = "**/*" if recursive else "*"

    for path in directory.glob(pattern):
        if path.is_file() and path.suffix.lower() in extensions:
            yield path


def scan_multiple_directories(
    directories: list[Path],
    recursive: bool = True,
    extensions: set[str] | None = None
) -> Iterator[Path]:
    """
    Scan multiple directories for image files.

    Args:
        directories: List of directories to scan
        recursive: Whether to scan subdirectories
        extensions: Custom set of extensions to look for

    Yields:
        Path objects for each image file found
    """
    seen = set()

    for directory in directories:
        if not directory.exists():
            continue

        for path in scan_directory(directory, recursive, extensions):
            # Avoid duplicates if directories overlap
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield path
