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


def load_report(report_path: Path) -> dict:
    """Load a JSON report file."""
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_report() -> None:
    """Save the current report back to disk."""
    global _current_report, _report_path
    if _current_report and _report_path:
        with open(_report_path, "w", encoding="utf-8") as f:
            json.dump(_current_report, f, indent=2, ensure_ascii=False)


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

    if category not in ["keep", "review", "trash"]:
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


@app.route("/api/delete", methods=["POST"])
def delete_image():
    """Delete an image file."""
    global _current_report

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])
    category = data.get("category", "")

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        os.remove(file_path)

        # Update the report
        if _current_report and category:
            for item in _current_report.get(category, []):
                if item.get("path") == str(file_path) or item.get("original_path") == str(file_path):
                    item["deleted"] = True
                    item["status"] = "deleted"
                    break
            save_report()

        return jsonify({"success": True, "message": f"Deleted {file_path}"})
    except PermissionError:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/keep", methods=["POST"])
def keep_image():
    """Move an image to the KEEP folder."""
    global _current_report, _report_path

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])
    from_category = data.get("category", "")

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        # Create KEEP folder next to the report
        keep_folder = _report_path.parent / "KEEP"
        keep_folder.mkdir(exist_ok=True)

        # Move file
        dest_path = keep_folder / file_path.name
        # Handle name conflicts
        counter = 1
        while dest_path.exists():
            dest_path = keep_folder / f"{file_path.stem}_{counter}{file_path.suffix}"
            counter += 1

        shutil.move(str(file_path), str(dest_path))

        # Update the report
        if _current_report and from_category:
            for item in _current_report.get(from_category, []):
                current_path = item.get("path")
                if current_path == str(file_path):
                    if "original_path" not in item:
                        item["original_path"] = current_path
                    item["path"] = str(dest_path)
                    item["moved_to"] = str(dest_path)
                    item["status"] = "kept"
                    break
            save_report()

        return jsonify({"success": True, "new_path": str(dest_path)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/organize", methods=["POST"])
def organize_all():
    """Move all images to their respective folders (KEEP, REVIEW, TRASH)."""
    global _current_report, _report_path

    if not _current_report or not _report_path:
        return jsonify({"success": False, "error": "No report loaded"}), 500

    try:
        base_folder = _report_path.parent
        results = {"moved": 0, "errors": [], "skipped": 0}

        for category in ["keep", "review", "trash"]:
            folder = base_folder / category.upper()
            folder.mkdir(exist_ok=True)

            for item in _current_report.get(category, []):
                # Skip already processed items
                if item.get("status") in ["deleted", "kept", "moved"]:
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

        return jsonify({
            "success": True,
            "moved": results["moved"],
            "skipped": results["skipped"],
            "errors": len(results["errors"]),
            "folders": {
                "keep": str(base_folder / "KEEP"),
                "review": str(base_folder / "REVIEW"),
                "trash": str(base_folder / "TRASH"),
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/delete-trash", methods=["POST"])
def delete_all_trash():
    """Delete all files in the TRASH category."""
    global _current_report

    if not _current_report:
        return jsonify({"success": False, "error": "No report loaded"}), 500

    try:
        results = {"deleted": 0, "errors": [], "skipped": 0}

        for item in _current_report.get("trash", []):
            if item.get("status") == "deleted":
                results["skipped"] += 1
                continue

            file_path = Path(item["path"])
            if not file_path.exists():
                results["skipped"] += 1
                continue

            try:
                os.remove(file_path)
                item["status"] = "deleted"
                item["deleted"] = True
                results["deleted"] += 1
            except Exception as e:
                results["errors"].append({"path": str(file_path), "error": str(e)})

        save_report()

        return jsonify({
            "success": True,
            "deleted": results["deleted"],
            "skipped": results["skipped"],
            "errors": len(results["errors"])
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def generate_classify_html(report: dict) -> str:
    """Generate HTML for classification review with lazy loading."""
    summary = report.get("summary", {})

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
        .subtitle {{ text-align: center; color: #888; margin-bottom: 30px; }}

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
        .action-btn.delete-trash {{ background: #e74c3c; }}
        .action-btn.delete-visible {{ background: #c0392b; }}

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
            width: 260px;
            transition: transform 0.2s;
        }}

        .image-card:hover {{ transform: scale(1.02); }}
        .image-card.keep {{ border: 3px solid #4ecca3; }}
        .image-card.review {{ border: 3px solid #f39c12; }}
        .image-card.trash {{ border: 3px solid #e74c3c; }}
        .image-card.deleted {{ border: 3px solid #666; opacity: 0.3; }}
        .image-card.kept {{ border: 3px solid #2ecc71; opacity: 0.5; }}
        .image-card.moved {{ border: 3px solid #9b59b6; opacity: 0.6; }}

        .image-container {{
            width: 100%;
            height: 180px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
            cursor: pointer;
        }}

        .image-container:hover {{ opacity: 0.9; }}
        .image-container img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}

        .image-placeholder {{ color: #444; font-size: 2em; }}
        .image-loading {{ color: #666; font-size: 0.9em; }}

        .image-info {{ padding: 10px; }}
        .image-path {{
            font-size: 0.7em;
            color: #888;
            word-break: break-all;
            margin-bottom: 6px;
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
            font-size: 0.65em;
            color: #aaa;
            max-width: 80px;
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
            padding: 5px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.7em;
            font-weight: bold;
        }}

        .btn-keep {{ background: #4ecca3; color: #1a1a2e; }}
        .btn-keep:hover {{ background: #3dbb92; }}
        .btn-delete {{ background: #e74c3c; color: #fff; }}
        .btn-delete:hover {{ background: #c0392b; }}
        .btn:disabled {{ background: #666; color: #999; cursor: not-allowed; }}

        .status-badge {{
            font-size: 0.65em;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: bold;
        }}
        .status-badge.deleted {{ background: #666; color: #fff; }}
        .status-badge.kept {{ background: #2ecc71; color: #fff; }}
        .status-badge.moved {{ background: #9b59b6; color: #fff; }}

        .empty {{ color: #666; font-style: italic; padding: 40px; text-align: center; width: 100%; }}

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
    <p class="subtitle">AI-powered classification | Infinite scroll</p>

    <div class="summary">
        <div class="summary-item">
            <div class="value">{summary.get("total_images", 0)}</div>
            <div class="label">Total Images</div>
        </div>
        <div class="summary-item">
            <div class="value keep" id="keep-count">{summary.get("keep_count", 0)}</div>
            <div class="label">KEEP (Faces)</div>
        </div>
        <div class="summary-item">
            <div class="value review" id="review-count">{summary.get("review_count", 0)}</div>
            <div class="label">REVIEW</div>
        </div>
        <div class="summary-item">
            <div class="value trash" id="trash-count">{summary.get("trash_count", 0)}</div>
            <div class="label">TRASH</div>
        </div>
    </div>

    <div class="actions">
        <button class="action-btn organize" onclick="organizeAll()">Organize All to Folders</button>
        <button class="action-btn delete-trash" onclick="deleteAllTrash()">Delete All TRASH</button>
        <button class="action-btn delete-visible" onclick="deleteAllVisible()">Delete Visible</button>
        <span class="action-info">Organize creates KEEP/, REVIEW/, TRASH/ folders and moves files</span>
    </div>

    <div class="tabs">
        <button class="tab active" onclick="showTab('trash')">TRASH <span class="count" id="trash-tab-count">{summary.get("trash_count", 0)}</span></button>
        <button class="tab" onclick="showTab('review')">REVIEW <span class="count" id="review-tab-count">{summary.get("review_count", 0)}</span></button>
        <button class="tab" onclick="showTab('keep')">KEEP <span class="count" id="keep-tab-count">{summary.get("keep_count", 0)}</span></button>
    </div>

    <div id="trash-section" class="section active">
        <h2 class="section-title trash">TRASH - Probably Delete</h2>
        <p class="section-desc">Screenshots, memes, graphics - probably safe to delete</p>
        <div id="trash-grid" class="images-grid"></div>
        <div id="trash-sentinel" class="scroll-sentinel"></div>
    </div>

    <div id="review-section" class="section">
        <h2 class="section-title review">REVIEW - Check Manually</h2>
        <p class="section-desc">Real photos without faces - review before deleting</p>
        <div id="review-grid" class="images-grid"></div>
        <div id="review-sentinel" class="scroll-sentinel"></div>
    </div>

    <div id="keep-section" class="section">
        <h2 class="section-title keep">KEEP - Family Photos</h2>
        <p class="section-desc">Photos with faces detected - probably want to keep</p>
        <div id="keep-grid" class="images-grid"></div>
        <div id="keep-sentinel" class="scroll-sentinel"></div>
    </div>

    <footer>
        Generated by <a href="https://github.com/fpelliccioni/image-dedup">image-dedup</a>
    </footer>

    <script>
        const PAGE_SIZE = 50;
        const data = {{ trash: [], review: [], keep: [] }};
        const totals = {{ trash: {summary.get("trash_count", 0)}, review: {summary.get("review_count", 0)}, keep: {summary.get("keep_count", 0)} }};
        const loaded = {{ trash: 0, review: 0, keep: 0 }};
        const counts = {{ trash: {summary.get("trash_count", 0)}, review: {summary.get("review_count", 0)}, keep: {summary.get("keep_count", 0)} }};
        let currentTab = 'trash';
        let loading = false;
        let fullyLoaded = {{ trash: false, review: false, keep: false }};

        // Intersection Observer for lazy loading images
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

        // Intersection Observer for infinite scroll
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

        // Observe all sentinels
        ['trash', 'review', 'keep'].forEach(cat => {{
            scrollObserver.observe(document.getElementById(cat + '-sentinel'));
        }});

        function getStatus(item) {{
            if (item.deleted || item.status === 'deleted') return 'deleted';
            if (item.status === 'kept') return 'kept';
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

            const card = document.createElement('div');
            card.className = 'image-card ' + (status === 'active' ? category : status);
            card.dataset.path = path;
            card.dataset.originalPath = originalPath;
            card.id = 'card-' + btoa(originalPath).replace(/[^a-zA-Z0-9]/g, '');

            const faceHtml = faceCount > 0 ? `<span class="face-count">${{faceCount}}</span>` : '';

            let buttonsHtml = '';
            if (status === 'active') {{
                buttonsHtml = `
                    <button class="btn btn-keep" onclick="keepImage('${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}',' ${{category}}', this)">Keep</button>
                    <button class="btn btn-delete" onclick="deleteImage('${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}',' ${{category}}', this)">Del</button>
                `;
            }} else {{
                const statusClass = status;
                const statusText = status.toUpperCase();
                buttonsHtml = `<span class="status-badge ${{statusClass}}">${{statusText}}</span>`;
            }}

            card.innerHTML = `
                <div class="image-container" onclick="openLightbox('${{encodedPath}}', '${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}')" >
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
            imageObserver.observe(img);

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

                // Update counts based on actual data
                counts[category] = result.total - data[category].filter(i => i.deleted || i.status === 'deleted').length;
                updateCounts();
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

            if (loaded[tab] === 0) {{
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
            document.getElementById('trash-tab-count').textContent = counts.trash;
            document.getElementById('review-tab-count').textContent = counts.review;
            document.getElementById('keep-tab-count').textContent = counts.keep;
        }}

        function markCard(card, status) {{
            card.classList.remove('keep', 'review', 'trash', 'deleted', 'kept', 'moved');
            card.classList.add(status);
            const meta = card.querySelector('.image-meta');
            const btns = meta.querySelectorAll('.btn');
            btns.forEach(b => b.remove());
            const badge = document.createElement('span');
            badge.className = 'status-badge ' + status;
            badge.textContent = status.toUpperCase();
            meta.appendChild(badge);
        }}

        function deleteImage(path, category, button) {{
            button.disabled = true;

            fetch('/api/delete', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path, category: category.trim() }}),
            }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Deleted: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    markCard(card, 'deleted');
                    counts[category.trim()]--;
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

        function keepImage(path, category, button) {{
            button.disabled = true;

            fetch('/api/keep', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path, category: category.trim() }}),
            }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Moved to KEEP: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    markCard(card, 'kept');
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

        function organizeAll() {{
            if (!confirm('Move ALL images to KEEP/, REVIEW/, TRASH/ folders?\\n\\nThis will organize all ' + (counts.keep + counts.review + counts.trash) + ' images.')) return;

            showToast('Organizing files...', 'info');

            fetch('/api/organize', {{ method: 'POST' }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast(`Organized! Moved: ${{result.moved}}, Skipped: ${{result.skipped}}, Errors: ${{result.errors}}`, 'success');
                    // Reload page to refresh state
                    setTimeout(() => location.reload(), 2000);
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                }}
            }})
            .catch(error => showToast('Error: ' + error.message, 'error'));
        }}

        function deleteAllTrash() {{
            if (!confirm('DELETE ALL ' + counts.trash + ' TRASH images?\\n\\nThis cannot be undone!')) return;

            showToast('Deleting trash files...', 'info');

            fetch('/api/delete-trash', {{ method: 'POST' }})
            .then(r => r.json())
            .then(result => {{
                if (result.success) {{
                    showToast(`Deleted: ${{result.deleted}}, Skipped: ${{result.skipped}}, Errors: ${{result.errors}}`, 'success');
                    counts.trash = 0;
                    updateCounts();
                    // Mark all visible trash as deleted
                    document.querySelectorAll('#trash-grid .image-card:not(.deleted)').forEach(card => {{
                        markCard(card, 'deleted');
                    }});
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                }}
            }})
            .catch(error => showToast('Error: ' + error.message, 'error'));
        }}

        function deleteAllVisible() {{
            const grid = document.getElementById(currentTab + '-grid');
            const cards = grid.querySelectorAll('.image-card:not(.deleted):not(.kept):not(.moved)');
            if (cards.length === 0) {{
                showToast('No active images to delete', 'error');
                return;
            }}
            if (!confirm('Delete ' + cards.length + ' visible images?\\n\\nThis cannot be undone!')) return;

            let deleted = 0;
            cards.forEach(card => {{
                const path = card.dataset.path;
                fetch('/api/delete', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ path: path, category: currentTab }}),
                }})
                .then(r => r.json())
                .then(result => {{
                    if (result.success) {{
                        markCard(card, 'deleted');
                        counts[currentTab]--;
                        updateCounts();
                        deleted++;
                    }}
                }});
            }});

            showToast('Deleting ' + cards.length + ' files...', 'info');
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeLightbox();
        }});

        // Load initial batch
        loadMore('trash');
    </script>
</body>
</html>
'''


def run_classify_server(report_path: Path, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the classification review server."""
    global _current_report, _report_path

    logging.info(f"Loading report: {report_path}")
    _report_path = report_path
    _current_report = load_report(report_path)

    summary = _current_report.get("summary", {})
    logging.info(f"Report loaded - KEEP: {summary.get('keep_count', 0)}, REVIEW: {summary.get('review_count', 0)}, TRASH: {summary.get('trash_count', 0)}")
    logging.info(f"Starting server at http://{host}:{port}")

    app.run(host=host, port=port, debug=False, threaded=True)
