# image-dedup

Detect duplicate and similar images to free up disk space.

## Features

- **Exact duplicate detection**: Uses SHA256 hash to find bit-for-bit identical files
- **Similar image detection**: Uses perceptual hashing (pHash + dHash) to find:
  - Resized versions of the same image
  - Re-compressed images (JPEG quality differences)
  - Minor edits and color adjustments
- **Fast scanning**: Processes thousands of images efficiently
- **Resumable**: Progress is saved automatically - interrupted scans resume where they left off
- **Safe operations**: Shows what would be deleted before taking action
- **Flexible output**: Terminal display, JSON export, or automatic file organization

## Installation

```bash
# Clone the repository
git clone https://github.com/fpelliccioni/image-dedup.git
cd image-dedup

# Install with pip
pip install -e .

# Or with uv (recommended)
uv pip install -e .
```

### NixOS

On NixOS, use the provided `shell.nix`:

```bash
git clone https://github.com/fpelliccioni/image-dedup.git
cd image-dedup
nix-shell
image-dedup scan ~/Photos
```

The shell automatically creates a virtualenv and sets up the required library paths.

**Alternative (without nix-shell):**

```bash
# Find your gcc lib path
find /nix/store -name "libstdc++.so.6" 2>/dev/null | head -1

# Export it and run
export LD_LIBRARY_PATH="/nix/store/<your-gcc-hash>-gcc-*-lib/lib:$LD_LIBRARY_PATH"
python -m venv .venv
source .venv/bin/activate
pip install -e .
image-dedup scan ~/Photos
```

## Usage

### Basic scan

```bash
# Scan a single directory
image-dedup scan ~/Photos

# Scan multiple directories
image-dedup scan ~/Photos ~/Downloads ~/Desktop
```

### Scan options

```bash
# Only find exact duplicates (faster)
image-dedup scan ~/Photos --exact-only

# Only find similar images
image-dedup scan ~/Photos --similar-only

# Don't scan subdirectories
image-dedup scan ~/Photos --no-recursive

# Adjust similarity threshold (0-64, lower = stricter)
# Default is 10, use 5 for stricter matching
image-dedup scan ~/Photos --threshold 5

# Output as JSON (for scripting)
image-dedup scan ~/Photos --json > duplicates.json

# Move duplicates to a folder for review
image-dedup scan ~/Photos --move-to ~/Duplicates

# Dry run (show what would be moved)
image-dedup scan ~/Photos --move-to ~/Duplicates --dry-run

# Disable caching (don't save progress)
image-dedup scan ~/Photos --no-cache
```

### Cache management

Progress is automatically saved to `~/.cache/image-dedup/cache.db`. If a scan is interrupted (e.g., by sleep/standby), it resumes where it left off.

```bash
# View cache statistics
image-dedup cache stats

# Clear all cached data
image-dedup cache clear
```

### Examples

```bash
# Find all duplicates in Photos, move them to a review folder
image-dedup scan ~/Photos --move-to ~/PhotoDuplicates --dry-run

# Strict similarity matching for photos
image-dedup scan ~/Photos --threshold 5

# Quick exact-duplicate scan of Downloads
image-dedup scan ~/Downloads --exact-only
```

## How It Works

### Exact Duplicates (SHA256)

Files are hashed using SHA256. Two files with the same hash are bit-for-bit identical, regardless of filename.

### Similar Images (Perceptual Hashing)

Uses a combination of:

- **pHash (Perceptual Hash)**: Analyzes the image's frequency components. Robust against resizing and compression.
- **dHash (Difference Hash)**: Compares adjacent pixel brightness. Good for detecting crops and minor edits.

The similarity is measured in Hamming distance (bits different):
- **0**: Identical images
- **1-10**: Very similar (likely same image with different compression/size)
- **11-20**: Somewhat similar
- **>20**: Different images

## Supported Formats

JPG, JPEG, PNG, GIF, BMP, TIFF, WebP, HEIC, HEIF, and various RAW formats (CR2, NEF, ARW, DNG, ORF, RW2, PEF, SR2).

## License

MIT
