"""Feedback learning system for personalized image classification."""

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# Default feedback database location
DEFAULT_FEEDBACK_PATH = Path.home() / ".image-dedup" / "feedback.db"

Decision = Literal["keep", "trash", "review"]


@dataclass
class FeedbackEntry:
    """A single feedback entry."""
    image_path: str
    decision: Decision
    embedding: np.ndarray
    clip_label: str
    face_count: int
    timestamp: str


class FeedbackStore:
    """SQLite-based storage for user feedback."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_FEEDBACK_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_path TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    clip_label TEXT,
                    face_count INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(image_path)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_decision ON feedback(decision)
            """)
            conn.commit()

    def add_feedback(
        self,
        image_path: str,
        decision: Decision,
        embedding: np.ndarray,
        clip_label: str = "",
        face_count: int = 0,
    ) -> bool:
        """
        Add or update feedback for an image.

        Returns True if added, False if updated.
        """
        embedding_blob = embedding.tobytes()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM feedback WHERE image_path = ?",
                (image_path,)
            )
            exists = cursor.fetchone() is not None

            if exists:
                conn.execute("""
                    UPDATE feedback
                    SET decision = ?, embedding = ?, clip_label = ?, face_count = ?,
                        timestamp = CURRENT_TIMESTAMP
                    WHERE image_path = ?
                """, (decision, embedding_blob, clip_label, face_count, image_path))
            else:
                conn.execute("""
                    INSERT INTO feedback (image_path, decision, embedding, clip_label, face_count)
                    VALUES (?, ?, ?, ?, ?)
                """, (image_path, decision, embedding_blob, clip_label, face_count))

            conn.commit()

        logger.info(f"Feedback {'updated' if exists else 'added'}: {decision} -> {image_path}")
        return not exists

    def get_all_feedback(self) -> list[tuple[np.ndarray, str]]:
        """Get all feedback as (embedding, decision) tuples."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT embedding, decision FROM feedback"
            )
            results = []
            for row in cursor.fetchall():
                embedding = np.frombuffer(row[0], dtype=np.float32)
                results.append((embedding, row[1]))
            return results

    def get_feedback_by_decision(self, decision: Decision) -> list[np.ndarray]:
        """Get all embeddings for a specific decision."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT embedding FROM feedback WHERE decision = ?",
                (decision,)
            )
            return [np.frombuffer(row[0], dtype=np.float32) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """Get feedback statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT decision, COUNT(*) FROM feedback GROUP BY decision
            """)
            counts = dict(cursor.fetchall())

            cursor = conn.execute("SELECT COUNT(*) FROM feedback")
            total = cursor.fetchone()[0]

            return {
                "total": total,
                "keep": counts.get("keep", 0),
                "trash": counts.get("trash", 0),
                "review": counts.get("review", 0),
                "db_path": str(self.db_path),
            }

    def clear(self) -> int:
        """Clear all feedback. Returns number of entries deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM feedback")
            count = cursor.fetchone()[0]
            conn.execute("DELETE FROM feedback")
            conn.commit()
            return count


class FeedbackClassifier:
    """
    Classifier trained on user feedback.

    Uses logistic regression on CLIP embeddings to learn user preferences.
    """

    def __init__(self, feedback_store: FeedbackStore | None = None):
        self.store = feedback_store or FeedbackStore()
        self.model = None
        self.is_trained = False
        self.min_samples = 10  # Minimum samples per class to train

    def can_train(self) -> tuple[bool, str]:
        """Check if we have enough data to train."""
        stats = self.store.get_stats()

        if stats["total"] < self.min_samples * 2:
            return False, f"Need at least {self.min_samples * 2} feedback samples (have {stats['total']})"

        # Need samples from at least 2 categories
        categories_with_data = sum(1 for k in ["keep", "trash"] if stats.get(k, 0) >= self.min_samples)
        if categories_with_data < 2:
            return False, f"Need at least {self.min_samples} samples each for 'keep' and 'trash'"

        return True, "Ready to train"

    def train(self) -> dict:
        """
        Train the classifier on accumulated feedback.

        Returns training statistics.
        """
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        can_train, reason = self.can_train()
        if not can_train:
            return {"success": False, "error": reason}

        # Get all feedback
        feedback = self.store.get_all_feedback()

        # Prepare training data
        # Map to binary: keep=1, trash=0
        # Skip "review" decisions - they're ambiguous
        X = []
        y = []

        for embedding, decision in feedback:
            if decision == "keep":
                X.append(embedding)
                y.append(1)
            elif decision == "trash":
                X.append(embedding)
                y.append(0)
            # Skip "review" - don't add to training data

        X = np.array(X)
        y = np.array(y)

        if len(X) < self.min_samples * 2:
            return {"success": False, "error": "Not enough keep/trash samples"}

        # Train with regularization to prevent overfitting
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = LogisticRegression(
            C=0.1,  # Regularization
            max_iter=1000,
            class_weight="balanced",  # Handle imbalanced data
        )
        self.model.fit(X_scaled, y)
        self.is_trained = True

        # Calculate training accuracy
        train_accuracy = self.model.score(X_scaled, y)

        stats = self.store.get_stats()
        return {
            "success": True,
            "samples_used": len(X),
            "keep_samples": sum(y),
            "trash_samples": len(y) - sum(y),
            "train_accuracy": round(train_accuracy, 3),
            "total_feedback": stats["total"],
        }

    def predict(self, embedding: np.ndarray) -> tuple[float, str]:
        """
        Predict keep probability for an embedding.

        Returns:
            (probability of keep, suggested decision)
        """
        if not self.is_trained or self.model is None:
            return 0.5, "unknown"

        embedding_scaled = self.scaler.transform(embedding.reshape(1, -1))
        prob = self.model.predict_proba(embedding_scaled)[0]

        # prob[1] is probability of keep (class 1)
        keep_prob = prob[1]

        if keep_prob > 0.7:
            decision = "keep"
        elif keep_prob < 0.3:
            decision = "trash"
        else:
            decision = "review"

        return float(keep_prob), decision

    def predict_batch(self, embeddings: list[np.ndarray]) -> list[tuple[float, str]]:
        """Predict for multiple embeddings."""
        if not self.is_trained or self.model is None:
            return [(0.5, "unknown")] * len(embeddings)

        X = np.array(embeddings)
        X_scaled = self.scaler.transform(X)
        probs = self.model.predict_proba(X_scaled)

        results = []
        for prob in probs:
            keep_prob = prob[1]
            if keep_prob > 0.7:
                decision = "keep"
            elif keep_prob < 0.3:
                decision = "trash"
            else:
                decision = "review"
            results.append((float(keep_prob), decision))

        return results

    def save(self, path: Path) -> None:
        """Save the trained model."""
        if not self.is_trained:
            raise ValueError("Model not trained")

        import pickle
        with open(path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "scaler": self.scaler,
            }, f)

    def load(self, path: Path) -> bool:
        """Load a trained model. Returns True if successful."""
        if not path.exists():
            return False

        import pickle
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
                self.model = data["model"]
                self.scaler = data["scaler"]
                self.is_trained = True
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False


def get_clip_embedding(image_path: Path) -> np.ndarray | None:
    """
    Get CLIP embedding for an image.

    Returns None if failed.
    """
    try:
        import torch
        import open_clip
        from PIL import Image

        # Register HEIC support
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass

        # Load model (cached globally in classifier module)
        from .classifier import _load_clip
        model, preprocess, _ = _load_clip()
        device = next(model.parameters()).device

        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            image_tensor = preprocess(img).unsqueeze(0).to(device)

        with torch.no_grad():
            embedding = model.encode_image(image_tensor)
            embedding = embedding / embedding.norm(dim=-1, keepdim=True)
            return embedding[0].cpu().numpy().astype(np.float32)

    except Exception as e:
        logger.error(f"Failed to get embedding for {image_path}: {e}")
        return None
