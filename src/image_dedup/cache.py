"""Cache module for storing computed hashes persistently."""

import sqlite3
from pathlib import Path
from typing import NamedTuple

import imagehash


class CachedImage(NamedTuple):
    """Cached hash data for an image."""
    path: str
    size: int
    mtime: float
    sha256: str | None
    phash: str | None
    dhash: str | None


class HashCache:
    """
    Persistent cache for image hashes using SQLite.

    Saves progress incrementally so work isn't lost if the process is interrupted.
    Uses file path, size, and mtime to detect if a file has changed.
    """

    def __init__(self, cache_path: Path | None = None):
        """
        Initialize the cache.

        Args:
            cache_path: Path to cache file. If None, uses ~/.cache/image-dedup/cache.db
        """
        if cache_path is None:
            cache_dir = Path.home() / ".cache" / "image-dedup"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / "cache.db"

        self.cache_path = cache_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        self._conn = sqlite3.connect(self.cache_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS image_hashes (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                sha256 TEXT,
                phash TEXT,
                dhash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sha256 ON image_hashes(sha256)
        """)
        self._conn.commit()

    def get(self, path: Path) -> CachedImage | None:
        """
        Get cached hashes for a file if they exist and are still valid.

        Args:
            path: Path to the image file

        Returns:
            CachedImage if found and valid, None otherwise
        """
        try:
            stat = path.stat()
            current_size = stat.st_size
            current_mtime = stat.st_mtime
        except OSError:
            return None

        cursor = self._conn.execute(
            "SELECT path, size, mtime, sha256, phash, dhash FROM image_hashes WHERE path = ?",
            (str(path),)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        cached = CachedImage(*row)

        # Check if file has changed
        if cached.size != current_size or abs(cached.mtime - current_mtime) > 0.001:
            # File changed, invalidate cache
            self.delete(path)
            return None

        return cached

    def set(
        self,
        path: Path,
        size: int,
        mtime: float,
        sha256: str | None = None,
        phash: imagehash.ImageHash | None = None,
        dhash: imagehash.ImageHash | None = None,
    ) -> None:
        """
        Store hashes for a file.

        Args:
            path: Path to the image file
            size: File size in bytes
            mtime: File modification time
            sha256: SHA256 hash (optional)
            phash: Perceptual hash (optional)
            dhash: Difference hash (optional)
        """
        phash_str = str(phash) if phash is not None else None
        dhash_str = str(dhash) if dhash is not None else None

        self._conn.execute("""
            INSERT OR REPLACE INTO image_hashes (path, size, mtime, sha256, phash, dhash)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(path), size, mtime, sha256, phash_str, dhash_str))
        self._conn.commit()

    def delete(self, path: Path) -> None:
        """Remove a file from the cache."""
        self._conn.execute("DELETE FROM image_hashes WHERE path = ?", (str(path),))
        self._conn.commit()

    def clear(self) -> int:
        """
        Clear all cached data.

        Returns:
            Number of entries deleted
        """
        cursor = self._conn.execute("SELECT COUNT(*) FROM image_hashes")
        count = cursor.fetchone()[0]
        self._conn.execute("DELETE FROM image_hashes")
        self._conn.commit()
        return count

    def stats(self) -> dict:
        """Get cache statistics."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM image_hashes")
        total = cursor.fetchone()[0]

        cursor = self._conn.execute("SELECT COUNT(*) FROM image_hashes WHERE sha256 IS NOT NULL")
        with_sha256 = cursor.fetchone()[0]

        cursor = self._conn.execute("SELECT COUNT(*) FROM image_hashes WHERE phash IS NOT NULL")
        with_phash = cursor.fetchone()[0]

        return {
            "total_entries": total,
            "with_sha256": with_sha256,
            "with_phash": with_phash,
            "cache_path": str(self.cache_path),
            "cache_size_bytes": self.cache_path.stat().st_size if self.cache_path.exists() else 0,
        }

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def phash_from_str(s: str) -> imagehash.ImageHash:
    """Convert a hex string back to an ImageHash."""
    return imagehash.hex_to_hash(s)
