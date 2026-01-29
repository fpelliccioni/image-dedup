"""Web server for reviewing and deleting classified images."""

import json
import logging
import os
import shutil
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote

from flask import Flask, jsonify, request, Response
from PIL import Image

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

THUMBNAIL_SIZE = (250, 250)
LIGHTBOX_SIZE = (1200, 1200)

app = Flask(__name__)

# Global state
_current_report: dict | None = None
_report_path: Path | None = None
_base_directory: Path | None = None


def load_report(report_path: Path) -> dict:
    """Load a JSON report file and separate deleted files."""
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    # Separate existing files from deleted ones
    deleted = []
    for category in ["keep", "review", "trash"]:
        existing = []
        for item in report.get(category, []):
            file_path = Path(item["path"])
            if file_path.exists():
                existing.append(item)
            else:
                item["original_category"] = category
                deleted.append(item)
        report[category] = existing

    report["deleted"] = deleted

    # Update summary counts
    if "summary" in report:
        report["summary"]["keep_count"] = len(report.get("keep", []))
        report["summary"]["review_count"] = len(report.get("review", []))
        report["summary"]["trash_count"] = len(report.get("trash", []))
        report["summary"]["deleted_count"] = len(deleted)

    return report


def save_report() -> None:
    """Save the current report back to disk."""
    global _current_report, _report_path
    if _current_report and _report_path:
        with open(_report_path, "w", encoding="utf-8") as f:
            json.dump(_current_report, f, indent=2, ensure_ascii=False)


def get_base_directory() -> Path:
    """Get the base directory for organizing files."""
    global _base_directory, _current_report
    if _base_directory:
        return _base_directory
    # Fallback to report's base_directory or report location
    if _current_report and _current_report.get("base_directory"):
        return Path(_current_report["base_directory"])
    return _report_path.parent if _report_path else Path.cwd()


def generate_image_bytes(image_path: Path, size: tuple[int, int]) -> bytes | None:
    """Generate a resized image as bytes."""
    try:
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail(size, Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            return buffer.read()
    except Exception:
        return None


def move_file_to_folder(file_path: Path, folder_name: str) -> Path:
    """Move a file to a folder in the base directory."""
    base = get_base_directory()
    dest_folder = base / folder_name
    dest_folder.mkdir(exist_ok=True)

    dest_path = dest_folder / file_path.name
    counter = 1
    while dest_path.exists():
        dest_path = dest_folder / f"{file_path.stem}_{counter}{file_path.suffix}"
        counter += 1

    shutil.move(str(file_path), str(dest_path))
    return dest_path


@app.route("/")
def index():
    """Serve the classification review page."""
    global _current_report
    logging.info("GET / - Serving main page")
    if _current_report is None:
        return "No report loaded", 500
    return generate_classify_html(_current_report)


@app.route("/api/data/<category>")
def get_category_data(category):
    """Get paginated data for a category."""
    global _current_report
    if _current_report is None:
        return jsonify({"error": "No report loaded"}), 500

    if category not in ["keep", "review", "trash", "deleted"]:
        return jsonify({"error": "Invalid category"}), 400

    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 50, type=int)

    items = _current_report.get(category, [])
    total = len(items)
    page_items = items[offset:offset + limit]

    logging.info(f"GET /api/data/{category} - offset={offset}, limit={limit}, returning {len(page_items)} items (total: {total})")

    return jsonify({
        "items": page_items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total
    })


@app.route("/api/thumbnail")
def get_thumbnail():
    """Serve a thumbnail image."""
    path = request.args.get("path")
    if not path:
        return "No path", 400

    file_path = Path(path)
    if not file_path.exists():
        return "Not found", 404

    img_bytes = generate_image_bytes(file_path, THUMBNAIL_SIZE)
    if img_bytes is None:
        return "Error loading image", 500

    return Response(img_bytes, mimetype="image/jpeg")


@app.route("/api/lightbox")
def get_lightbox():
    """Serve a lightbox image."""
    path = request.args.get("path")
    if not path:
        return "No path", 400

    file_path = Path(path)
    if not file_path.exists():
        return "Not found", 404

    img_bytes = generate_image_bytes(file_path, LIGHTBOX_SIZE)
    if img_bytes is None:
        return "Error loading image", 500

    return Response(img_bytes, mimetype="image/jpeg")


@app.route("/api/move-to-trash", methods=["POST"])
def move_to_trash():
    """Move an image to the Trash folder."""
    global _current_report

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])
    from_category = data.get("category", "")

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        dest_path = move_file_to_folder(file_path, "Trash")
        logging.info(f"Moved to Trash: {file_path} -> {dest_path}")

        # Update the report
        if _current_report and from_category:
            for item in _current_report.get(from_category, []):
                if item.get("path") == str(file_path):
                    if "original_path" not in item:
                        item["original_path"] = item["path"]
                    item["path"] = str(dest_path)
                    item["moved_to"] = str(dest_path)
                    item["status"] = "trashed"
                    break
            save_report()

        return jsonify({"success": True, "new_path": str(dest_path)})
    except Exception as e:
        logging.error(f"Error moving to trash: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/move-to-keep", methods=["POST"])
def move_to_keep():
    """Move an image to the Keep folder."""
    global _current_report

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])
    from_category = data.get("category", "")

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        dest_path = move_file_to_folder(file_path, "Keep")
        logging.info(f"Moved to Keep: {file_path} -> {dest_path}")

        # Update the report
        if _current_report and from_category:
            for item in _current_report.get(from_category, []):
                if item.get("path") == str(file_path):
                    if "original_path" not in item:
                        item["original_path"] = item["path"]
                    item["path"] = str(dest_path)
                    item["moved_to"] = str(dest_path)
                    item["status"] = "kept"
                    break
            save_report()

        return jsonify({"success": True, "new_path": str(dest_path)})
    except Exception as e:
        logging.error(f"Error moving to keep: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/move-to-review", methods=["POST"])
def move_to_review():
    """Move an image to the Review folder."""
    global _current_report

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])
    from_category = data.get("category", "")

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        dest_path = move_file_to_folder(file_path, "Review")
        logging.info(f"Moved to Review: {file_path} -> {dest_path}")

        # Update the report
        if _current_report and from_category:
            for item in _current_report.get(from_category, []):
                if item.get("path") == str(file_path):
                    if "original_path" not in item:
                        item["original_path"] = item["path"]
                    item["path"] = str(dest_path)
                    item["moved_to"] = str(dest_path)
                    item["status"] = "review"
                    break
            save_report()

        return jsonify({"success": True, "new_path": str(dest_path)})
    except Exception as e:
        logging.error(f"Error moving to review: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/organize", methods=["POST"])
def organize_all():
    """Move all images to their respective folders based on AI classification."""
    global _current_report

    if not _current_report:
        return jsonify({"success": False, "error": "No report loaded"}), 500

    try:
        base = get_base_directory()
        results = {"moved": 0, "errors": [], "skipped": 0}

        # Mapping: AI category -> folder name
        folder_map = {
            "keep": "Keep",
            "review": "Review",
            "trash": "Probably Delete"
        }

        for category, folder_name in folder_map.items():
            folder = base / folder_name
            folder.mkdir(exist_ok=True)

            for item in _current_report.get(category, []):
                # Skip already processed items
                if item.get("status") in ["trashed", "kept", "review", "moved"]:
                    results["skipped"] += 1
                    continue

                file_path = Path(item["path"])
                if not file_path.exists():
                    results["skipped"] += 1
                    continue

                try:
                    dest_path = folder / file_path.name
                    counter = 1
                    while dest_path.exists():
                        dest_path = folder / f"{file_path.stem}_{counter}{file_path.suffix}"
                        counter += 1

                    shutil.move(str(file_path), str(dest_path))

                    if "original_path" not in item:
                        item["original_path"] = item["path"]
                    item["path"] = str(dest_path)
                    item["moved_to"] = str(dest_path)
                    item["status"] = "moved"
                    results["moved"] += 1
                except Exception as e:
                    results["errors"].append({"path": str(file_path), "error": str(e)})

        save_report()
        logging.info(f"Organized: moved={results['moved']}, skipped={results['skipped']}, errors={len(results['errors'])}")

        return jsonify({
            "success": True,
            "moved": results["moved"],
            "skipped": results["skipped"],
            "errors": len(results["errors"]),
            "base_directory": str(base),
            "folders": {
                "keep": str(base / "Keep"),
                "review": str(base / "Review"),
                "probably_delete": str(base / "Probably Delete"),
            }
        })
    except Exception as e:
        logging.error(f"Error organizing: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def generate_classify_html(report: dict) -> str:
    """Generate HTML for classification review with lazy loading."""
    summary = report.get("summary", {})
    base_dir = report.get("base_directory", "")

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Image Classification Review</title>
    <style>
        * {{ box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            margin: 0;
            padding: 20px;
            line-height: 1.6;
        }}

        h1 {{ text-align: center; color: #fff; margin-bottom: 10px; }}
        .subtitle {{ text-align: center; color: #888; margin-bottom: 10px; }}
        .base-dir {{ text-align: center; color: #666; font-size: 0.85em; margin-bottom: 30px; word-break: break-all; }}

        .summary {{
            background: #16213e;
            border-radius: 12px;
            padding: 20px 30px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
            gap: 20px;
        }}

        .summary-item {{ text-align: center; }}
        .summary-item .value {{ font-size: 2em; font-weight: bold; }}
        .summary-item .value.keep {{ color: #4ecca3; }}
        .summary-item .value.review {{ color: #f39c12; }}
        .summary-item .value.trash {{ color: #e74c3c; }}
        .summary-item .value.deleted {{ color: #666; }}
        .summary-item .label {{ color: #888; font-size: 0.9em; }}

        .actions {{
            background: #16213e;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .action-btn {{
            color: #fff;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.9em;
        }}
        .action-btn:hover {{ opacity: 0.9; }}
        .action-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .action-btn.organize {{ background: #3498db; }}

        .action-info {{ color: #888; font-size: 0.85em; }}

        .tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}

        .tab {{
            background: #16213e;
            border: none;
            color: #888;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1em;
            font-weight: bold;
            transition: all 0.2s;
        }}

        .tab:hover {{ background: #1f2b4a; }}
        .tab.active {{ background: #4ecca3; color: #1a1a2e; }}
        .tab .count {{ margin-left: 8px; }}

        .section {{ display: none; }}
        .section.active {{ display: block; }}

        .section-title {{ border-bottom: 2px solid #333; padding-bottom: 10px; margin-top: 20px; }}
        .section-title.keep {{ color: #4ecca3; }}
        .section-title.review {{ color: #f39c12; }}
        .section-title.trash {{ color: #e74c3c; }}
        .section-title.deleted {{ color: #666; }}
        .section-title.trashed {{ color: #9b59b6; }}

        .section-desc {{ color: #888; margin-bottom: 20px; }}

        .images-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
        }}

        .image-card {{
            background: #0f0f23;
            border-radius: 8px;
            overflow: hidden;
            width: 280px;
            transition: transform 0.2s;
        }}

        .image-card:hover {{ transform: scale(1.02); }}
        .image-card.keep {{ border: 3px solid #4ecca3; }}
        .image-card.review {{ border: 3px solid #f39c12; }}
        .image-card.trash {{ border: 3px solid #e74c3c; }}
        .image-card.trashed {{ border: 3px solid #666; opacity: 0.4; }}
        .image-card.kept {{ border: 3px solid #2ecc71; opacity: 0.5; }}
        .image-card.moved {{ border: 3px solid #9b59b6; opacity: 0.6; }}
        .image-card.deleted-file {{ border: 3px solid #444; opacity: 0.5; background: #1a1a1a; }}

        .image-container {{
            width: 100%;
            height: 200px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
            cursor: pointer;
        }}

        .image-container:hover {{ opacity: 0.9; }}
        .image-container img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}

        .image-placeholder {{ color: #444; font-size: 2em; }}

        .image-info {{ padding: 12px; }}
        .image-path {{
            font-size: 0.75em;
            color: #888;
            word-break: break-all;
            margin-bottom: 8px;
            max-height: 2.5em;
            overflow: hidden;
        }}

        .image-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
        }}

        .image-label {{
            font-size: 0.7em;
            color: #aaa;
            max-width: 90px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .face-count {{
            background: #4ecca3;
            color: #1a1a2e;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.7em;
            font-weight: bold;
        }}

        .btn {{
            border: none;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.7em;
            font-weight: bold;
        }}

        .btn-trash {{ background: #e74c3c; color: #fff; }}
        .btn-trash:hover {{ background: #c0392b; }}
        .btn-keep {{ background: #4ecca3; color: #1a1a2e; }}
        .btn-keep:hover {{ background: #3dbb92; }}
        .btn-review {{ background: #f39c12; color: #1a1a2e; }}
        .btn-review:hover {{ background: #d68910; }}
        .btn-share {{ background: #25D366; color: #fff; }}
        .btn-share:hover {{ background: #1da851; }}
        .btn:disabled {{ background: #666; color: #999; cursor: not-allowed; }}

        .status-badge {{
            font-size: 0.65em;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: bold;
        }}
        .status-badge.trashed {{ background: #666; color: #fff; }}
        .status-badge.kept {{ background: #2ecc71; color: #fff; }}
        .status-badge.review {{ background: #f39c12; color: #1a1a2e; }}
        .status-badge.moved {{ background: #9b59b6; color: #fff; }}
        .status-badge.deleted {{ background: #444; color: #aaa; }}

        .scroll-sentinel {{ height: 20px; width: 100%; }}

        footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #333;
            color: #666;
        }}
        footer a {{ color: #4ecca3; text-decoration: none; }}

        .lightbox {{
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.95);
            z-index: 1000;
            cursor: pointer;
        }}

        .lightbox.active {{
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
        }}

        .lightbox img {{ max-width: 90%; max-height: 85%; object-fit: contain; border-radius: 8px; }}
        .lightbox-info {{ color: #fff; margin-top: 15px; text-align: center; }}
        .lightbox-path {{ font-size: 0.9em; color: #888; word-break: break-all; max-width: 90%; }}
        .lightbox-close {{ position: absolute; top: 20px; right: 30px; font-size: 40px; color: #fff; cursor: pointer; opacity: 0.7; }}
        .lightbox-close:hover {{ opacity: 1; }}
        .lightbox-hint {{ position: absolute; bottom: 20px; color: #666; font-size: 0.9em; }}

        .toast {{
            position: fixed;
            bottom: 20px; right: 20px;
            padding: 15px 25px;
            border-radius: 8px;
            color: #fff;
            font-weight: bold;
            z-index: 2000;
            opacity: 0;
            transition: opacity 0.3s;
            max-width: 400px;
        }}
        .toast.show {{ opacity: 1; }}
        .toast.success {{ background: #4ecca3; color: #1a1a2e; }}
        .toast.error {{ background: #e74c3c; }}
        .toast.info {{ background: #3498db; }}
    </style>
</head>
<body>
    <div id="lightbox" class="lightbox" onclick="closeLightbox()">
        <span class="lightbox-close">&times;</span>
        <img id="lightbox-img" src="" alt="">
        <div class="lightbox-info">
            <div id="lightbox-path" class="lightbox-path"></div>
        </div>
        <div class="lightbox-hint">Click anywhere or press ESC to close</div>
    </div>

    <div id="toast" class="toast"></div>

    <h1>Image Classification Review</h1>
    <p class="subtitle">AI-powered classification | Safe mode - nothing is deleted</p>
    <p class="base-dir">Folders will be created in: {base_dir}</p>

    <div class="summary">
        <div class="summary-item">
            <div class="value">{summary.get("total_images", 0)}</div>
            <div class="label">Total Images</div>
        </div>
        <div class="summary-item">
            <div class="value keep" id="keep-count">{summary.get("keep_count", 0)}</div>
            <div class="label">Keep</div>
        </div>
        <div class="summary-item">
            <div class="value review" id="review-count">{summary.get("review_count", 0)}</div>
            <div class="label">Review</div>
        </div>
        <div class="summary-item">
            <div class="value trash" id="trash-count">{summary.get("trash_count", 0)}</div>
            <div class="label">Probably Delete</div>
        </div>
        <div class="summary-item">
            <div class="value deleted" id="deleted-count">{summary.get("deleted_count", 0)}</div>
            <div class="label">Deleted</div>
        </div>
    </div>

    <div class="actions">
        <button class="action-btn organize" onclick="organizeAll()">Organize All to Folders</button>
        <span class="action-info">Moves all images to Keep/, Review/, Probably Delete/ folders</span>
    </div>

    <div class="tabs">
        <button class="tab active" onclick="showTab('trash')">Probably Delete <span class="count" id="trash-tab-count">{summary.get("trash_count", 0)}</span></button>
        <button class="tab" onclick="showTab('review')">Review <span class="count" id="review-tab-count">{summary.get("review_count", 0)}</span></button>
        <button class="tab" onclick="showTab('keep')">Keep <span class="count" id="keep-tab-count">{summary.get("keep_count", 0)}</span></button>
        <button class="tab" onclick="showTab('trashed')">Trash <span class="count" id="trashed-tab-count">0</span></button>
        <button class="tab" onclick="showTab('deleted')">Deleted <span class="count" id="deleted-tab-count">{summary.get("deleted_count", 0)}</span></button>
    </div>

    <div id="trash-section" class="section active">
        <h2 class="section-title trash">Probably Delete</h2>
        <p class="section-desc">Screenshots, memes, graphics - AI thinks these can be deleted</p>
        <div id="trash-grid" class="images-grid"></div>
        <div id="trash-sentinel" class="scroll-sentinel"></div>
    </div>

    <div id="review-section" class="section">
        <h2 class="section-title review">Review</h2>
        <p class="section-desc">Real photos without faces - review these manually</p>
        <div id="review-grid" class="images-grid"></div>
        <div id="review-sentinel" class="scroll-sentinel"></div>
    </div>

    <div id="keep-section" class="section">
        <h2 class="section-title keep">Keep</h2>
        <p class="section-desc">Photos with faces detected - family photos</p>
        <div id="keep-grid" class="images-grid"></div>
        <div id="keep-sentinel" class="scroll-sentinel"></div>
    </div>

    <div id="trashed-section" class="section">
        <h2 class="section-title trashed">Moved to Trash</h2>
        <p class="section-desc">Images you marked for deletion - delete the Trash/ folder manually when ready</p>
        <div id="trashed-grid" class="images-grid"></div>
    </div>

    <div id="deleted-section" class="section">
        <h2 class="section-title deleted">Deleted from Disk</h2>
        <p class="section-desc">These files no longer exist on disk - already deleted</p>
        <div id="deleted-grid" class="images-grid"></div>
        <div id="deleted-sentinel" class="scroll-sentinel"></div>
    </div>

    <footer>
        Generated by <a href="https://github.com/fpelliccioni/image-dedup">image-dedup</a>
    </footer>

    <script>
        const PAGE_SIZE = 50;
        const data = {{ trash: [], review: [], keep: [], deleted: [] }};
        const totals = {{ trash: {summary.get("trash_count", 0)}, review: {summary.get("review_count", 0)}, keep: {summary.get("keep_count", 0)}, deleted: {summary.get("deleted_count", 0)} }};
        const loaded = {{ trash: 0, review: 0, keep: 0, deleted: 0 }};
        const counts = {{ trash: {summary.get("trash_count", 0)}, review: {summary.get("review_count", 0)}, keep: {summary.get("keep_count", 0)}, deleted: {summary.get("deleted_count", 0)}, trashed: 0 }};
        const trashedItems = [];  // Items moved to Trash folder
        let currentTab = 'trash';
        let loading = false;
        let fullyLoaded = {{ trash: false, review: false, keep: false, deleted: false }};

        const imageObserver = new IntersectionObserver((entries) => {{
            entries.forEach(entry => {{
                if (entry.isIntersecting) {{
                    const img = entry.target;
                    const src = img.dataset.src;
                    if (src) {{
                        img.src = src;
                        img.removeAttribute('data-src');
                        imageObserver.unobserve(img);
                    }}
                }}
            }});
        }}, {{ rootMargin: '300px' }});

        const scrollObserver = new IntersectionObserver((entries) => {{
            entries.forEach(entry => {{
                if (entry.isIntersecting && !loading) {{
                    const category = entry.target.id.replace('-sentinel', '');
                    if (category === currentTab) {{
                        loadMore(category);
                    }}
                }}
            }});
        }}, {{ rootMargin: '500px' }});

        ['trash', 'review', 'keep', 'deleted'].forEach(cat => {{
            scrollObserver.observe(document.getElementById(cat + '-sentinel'));
        }});

        function getStatus(item) {{
            if (item.status === 'trashed') return 'trashed';
            if (item.status === 'kept') return 'kept';
            if (item.status === 'review') return 'review';
            if (item.status === 'moved') return 'moved';
            return 'active';
        }}

        function createCard(item, category) {{
            const path = item.path;
            const originalPath = item.original_path || path;
            const clipLabel = item.clip_label || 'unknown';
            const faceCount = item.face_count || 0;
            const shortLabel = clipLabel.replace('a ', '').replace('photograph of ', '').substring(0, 15);
            const fileName = path.split(/[\\\\/]/).pop();
            const encodedPath = encodeURIComponent(path);
            const status = getStatus(item);
            const isDeleted = category === 'deleted';
            const originalCategory = item.original_category || '';

            const card = document.createElement('div');
            card.className = 'image-card ' + (isDeleted ? 'deleted-file' : (status === 'active' ? category : status));
            card.dataset.path = path;
            card.dataset.originalPath = originalPath;
            card.id = 'card-' + btoa(unescape(encodeURIComponent(originalPath))).replace(/[^a-zA-Z0-9]/g, '');

            const faceHtml = faceCount > 0 ? `<span class="face-count">${{faceCount}}</span>` : '';

            let buttonsHtml = '';
            const escapedPath = path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");

            if (isDeleted) {{
                // No buttons for deleted files, just show original category
                const catLabel = originalCategory === 'trash' ? 'Prob.Del' : originalCategory.charAt(0).toUpperCase() + originalCategory.slice(1);
                buttonsHtml = `<span class="status-badge deleted">Was: ${{catLabel}}</span>`;
            }} else if (status === 'active') {{
                buttonsHtml = `
                    <button class="btn btn-trash" onclick="moveToTrash('${{escapedPath}}', '${{category}}', this)" title="Move to Trash folder">Del</button>
                    <button class="btn btn-keep" onclick="moveToKeep('${{escapedPath}}', '${{category}}', this)" title="Move to Keep folder">Keep</button>
                    <button class="btn btn-review" onclick="moveToReview('${{escapedPath}}', '${{category}}', this)" title="Move to Review folder">Later</button>
                    <button class="btn btn-share" onclick="shareWhatsApp('${{escapedPath}}')" title="Share via WhatsApp">WA</button>
                `;
            }} else {{
                const statusText = status === 'trashed' ? 'TRASH' : status.toUpperCase();
                buttonsHtml = `<span class="status-badge ${{status}}">${{statusText}}</span>`;
            }}

            if (isDeleted) {{
                // For deleted files, show a placeholder instead of trying to load the image
                card.innerHTML = `
                    <div class="image-container" style="cursor: default;">
                        <span class="image-placeholder">🗑️</span>
                    </div>
                    <div class="image-info">
                        <div class="image-path" title="${{path}}">${{fileName}}</div>
                        <div class="image-meta">
                            <span class="image-label" title="${{clipLabel}}">${{shortLabel}}</span>
                            ${{faceHtml}}
                            ${{buttonsHtml}}
                        </div>
                    </div>
                `;
            }} else {{
                card.innerHTML = `
                    <div class="image-container" onclick="openLightbox('${{encodedPath}}', '${{escapedPath}}')" >
                        <img data-src="/api/thumbnail?path=${{encodedPath}}" alt="${{fileName}}" class="lazy-image">
                    </div>
                    <div class="image-info">
                        <div class="image-path" title="${{path}}">${{fileName}}</div>
                        <div class="image-meta">
                            <span class="image-label" title="${{clipLabel}}">${{shortLabel}}</span>
                            ${{faceHtml}}
                            ${{buttonsHtml}}
                        </div>
                    </div>
                `;

                const img = card.querySelector('.lazy-image');
                if (img) imageObserver.observe(img);
            }}

            return card;
        }}

        async function loadMore(category) {{
            if (loading || fullyLoaded[category]) return;
            loading = true;

            const grid = document.getElementById(category + '-grid');
            const offset = loaded[category];

            try {{
                const response = await fetch(`/api/data/${{category}}?offset=${{offset}}&limit=${{PAGE_SIZE}}`);
                const result = await response.json();

                result.items.forEach(item => {{
                    data[category].push(item);
                    grid.appendChild(createCard(item, category));
                }});

                loaded[category] = offset + result.items.length;
                fullyLoaded[category] = !result.has_more;
            }} catch (error) {{
                console.error('Error loading data:', error);
            }}

            loading = false;
        }}

        function showTab(tab) {{
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(tab + '-section').classList.add('active');
            event.target.classList.add('active');
            currentTab = tab;

            // trashed tab is populated dynamically, no API loading needed
            if (tab !== 'trashed' && loaded[tab] === 0) {{
                loadMore(tab);
            }}
        }}

        function openLightbox(encodedPath, displayPath) {{
            document.getElementById('lightbox-img').src = '/api/lightbox?path=' + encodedPath;
            document.getElementById('lightbox-path').textContent = displayPath;
            document.getElementById('lightbox').classList.add('active');
            document.body.style.overflow = 'hidden';
        }}

        function closeLightbox() {{
            document.getElementById('lightbox').classList.remove('active');
            document.body.style.overflow = '';
        }}

        function showToast(message, type) {{
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast ' + type + ' show';
            setTimeout(() => toast.classList.remove('show'), 4000);
        }}

        function updateCounts() {{
            document.getElementById('trash-count').textContent = counts.trash;
            document.getElementById('review-count').textContent = counts.review;
            document.getElementById('keep-count').textContent = counts.keep;
            document.getElementById('deleted-count').textContent = counts.deleted;
            document.getElementById('trash-tab-count').textContent = counts.trash;
            document.getElementById('review-tab-count').textContent = counts.review;
            document.getElementById('keep-tab-count').textContent = counts.keep;
            document.getElementById('deleted-tab-count').textContent = counts.deleted;
            document.getElementById('trashed-tab-count').textContent = counts.trashed;
        }}

        function markCard(card, status) {{
            card.classList.remove('keep', 'review', 'trash', 'trashed', 'kept', 'moved');
            card.classList.add(status);
            const meta = card.querySelector('.image-meta');
            const btns = meta.querySelectorAll('.btn');
            btns.forEach(b => b.remove());
            const badge = document.createElement('span');
            badge.className = 'status-badge ' + status;
            badge.textContent = status === 'trashed' ? 'TRASH' : status.toUpperCase();
            meta.appendChild(badge);
        }}

        function moveToTrash(path, category, button) {{
            button.disabled = true;

            fetch('/api/move-to-trash', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path, category: category }}),
            }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Moved to Trash: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');

                    // Get item data before removing
                    const item = {{ path: result.new_path, original_path: path, status: 'trashed' }};
                    trashedItems.push(item);

                    // Add to trashed grid
                    const trashedGrid = document.getElementById('trashed-grid');
                    trashedGrid.appendChild(createTrashedCard(item));

                    // Remove from original grid
                    card.remove();

                    counts[category]--;
                    counts.trashed++;
                    updateCounts();
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                    button.disabled = false;
                }}
            }})
            .catch(error => {{
                showToast('Error: ' + error.message, 'error');
                button.disabled = false;
            }});
        }}

        function createTrashedCard(item) {{
            const path = item.path;
            const originalPath = item.original_path || path;
            const fileName = path.split(/[\\\\/]/).pop();
            const encodedPath = encodeURIComponent(path);

            const card = document.createElement('div');
            card.className = 'image-card trashed';
            card.dataset.path = path;

            card.innerHTML = `
                <div class="image-container" onclick="openLightbox('${{encodedPath}}', '${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}')" >
                    <img src="/api/thumbnail?path=${{encodedPath}}" alt="${{fileName}}">
                </div>
                <div class="image-info">
                    <div class="image-path" title="${{path}}">${{fileName}}</div>
                    <div class="image-meta">
                        <span class="status-badge trashed">IN TRASH</span>
                    </div>
                </div>
            `;

            return card;
        }}

        function moveToKeep(path, category, button) {{
            button.disabled = true;

            fetch('/api/move-to-keep', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path, category: category }}),
            }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Moved to Keep: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    card.remove();
                    counts[category]--;
                    updateCounts();
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                    button.disabled = false;
                }}
            }})
            .catch(error => {{
                showToast('Error: ' + error.message, 'error');
                button.disabled = false;
            }});
        }}

        function moveToReview(path, category, button) {{
            button.disabled = true;

            fetch('/api/move-to-review', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path, category: category }}),
            }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Moved to Review: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    card.remove();
                    counts[category]--;
                    updateCounts();
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                    button.disabled = false;
                }}
            }})
            .catch(error => {{
                showToast('Error: ' + error.message, 'error');
                button.disabled = false;
            }});
        }}

        function shareWhatsApp(path) {{
            const fileName = path.split(/[\\\\/]/).pop();
            const message = encodeURIComponent('Check this image: ' + fileName + '\\n\\nPath: ' + path);
            window.open('https://web.whatsapp.com/send?text=' + message, '_blank');
        }}

        function organizeAll() {{
            const total = counts.keep + counts.review + counts.trash;
            if (!confirm('Move all ' + total + ' images to folders?\\n\\n- Keep/\\n- Review/\\n- Probably Delete/\\n\\nNo files will be deleted.')) return;

            showToast('Organizing files...', 'info');

            fetch('/api/organize', {{ method: 'POST' }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast(`Organized! Moved: ${{result.moved}}, Skipped: ${{result.skipped}}`, 'success');
                    setTimeout(() => location.reload(), 2000);
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                }}
            }})
            .catch(error => showToast('Error: ' + error.message, 'error'));
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeLightbox();
        }});

        loadMore('trash');
    </script>
</body>
</html>
'''


def run_classify_server(report_path: Path, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the classification review server."""
    global _current_report, _report_path, _base_directory

    logging.info(f"Loading report: {report_path}")
    _report_path = report_path
    _current_report = load_report(report_path)

    # Set base directory
    if _current_report.get("base_directory"):
        _base_directory = Path(_current_report["base_directory"])
        logging.info(f"Base directory: {_base_directory}")

    summary = _current_report.get("summary", {})
    logging.info(f"Report loaded - KEEP: {summary.get('keep_count', 0)}, REVIEW: {summary.get('review_count', 0)}, TRASH: {summary.get('trash_count', 0)}")
    logging.info(f"Starting server at http://{host}:{port}")

    app.run(host=host, port=port, debug=False, threaded=True)
