"""Web server for reviewing and deleting classified images."""

import json
import os
import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote

from flask import Flask, jsonify, request, Response
from PIL import Image

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
    if _current_report is None:
        return "No report loaded", 500
    return generate_classify_html(_current_report)


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
    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        os.remove(file_path)
        return jsonify({"success": True, "message": f"Deleted {file_path}"})
    except PermissionError:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def generate_classify_html(report: dict) -> str:
    """Generate HTML for classification review with lazy loading."""
    summary = report.get("summary", {})

    # Build JSON data for JavaScript
    keep_data = json.dumps(report.get("keep", []))
    review_data = json.dumps(report.get("review", []))
    trash_data = json.dumps(report.get("trash", []))

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
            margin-bottom: 30px;
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
        .image-card.deleted {{ border: 3px solid #666; opacity: 0.4; }}

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

        .image-placeholder {{
            color: #444;
            font-size: 2em;
        }}

        .image-loading {{
            color: #666;
            font-size: 0.9em;
        }}

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
            font-size: 0.7em;
            color: #aaa;
            max-width: 120px;
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

        .delete-btn {{
            background: #e74c3c;
            color: #fff;
            border: none;
            padding: 5px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75em;
            font-weight: bold;
        }}

        .delete-btn:hover {{ background: #c0392b; }}
        .delete-btn:disabled {{ background: #666; cursor: not-allowed; }}
        .delete-btn.deleted {{ background: #666; }}

        .empty {{ color: #666; font-style: italic; padding: 40px; text-align: center; width: 100%; }}

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
        }}
        .toast.show {{ opacity: 1; }}
        .toast.success {{ background: #4ecca3; color: #1a1a2e; }}
        .toast.error {{ background: #e74c3c; }}

        .bulk-actions {{
            background: #16213e;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }}

        .bulk-btn {{
            background: #e74c3c;
            color: #fff;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
        }}
        .bulk-btn:hover {{ background: #c0392b; }}

        .load-more {{
            width: 100%;
            padding: 15px;
            margin-top: 20px;
            background: #16213e;
            border: none;
            color: #4ecca3;
            font-size: 1em;
            font-weight: bold;
            border-radius: 8px;
            cursor: pointer;
        }}
        .load-more:hover {{ background: #1f2b4a; }}
        .load-more:disabled {{ color: #666; cursor: not-allowed; }}
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
    <p class="subtitle">AI-powered classification | Lazy loading enabled</p>

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

    <div class="bulk-actions">
        <button class="bulk-btn" onclick="deleteAllVisible()">Delete All Visible</button>
        <span style="color: #888;">Delete all currently loaded images in the active tab</span>
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
        <button id="trash-load-more" class="load-more" onclick="loadMore('trash')">Load More</button>
    </div>

    <div id="review-section" class="section">
        <h2 class="section-title review">REVIEW - Check Manually</h2>
        <p class="section-desc">Real photos without faces - review before deleting</p>
        <div id="review-grid" class="images-grid"></div>
        <button id="review-load-more" class="load-more" onclick="loadMore('review')">Load More</button>
    </div>

    <div id="keep-section" class="section">
        <h2 class="section-title keep">KEEP - Family Photos</h2>
        <p class="section-desc">Photos with faces detected - probably want to keep</p>
        <div id="keep-grid" class="images-grid"></div>
        <button id="keep-load-more" class="load-more" onclick="loadMore('keep')">Load More</button>
    </div>

    <footer>
        Generated by <a href="https://github.com/fpelliccioni/image-dedup">image-dedup</a>
    </footer>

    <script>
        const PAGE_SIZE = 50;
        const data = {{
            trash: {trash_data},
            review: {review_data},
            keep: {keep_data}
        }};
        const loaded = {{ trash: 0, review: 0, keep: 0 }};
        const deleted = {{ trash: new Set(), review: new Set(), keep: new Set() }};
        let currentTab = 'trash';

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
        }}, {{ rootMargin: '200px' }});

        function createCard(item, category) {{
            const path = item.path;
            const clipLabel = item.clip_label || 'unknown';
            const faceCount = item.face_count || 0;
            const shortLabel = clipLabel.replace('a ', '').replace('photograph of ', '').substring(0, 20);
            const fileName = path.split(/[\\\\/]/).pop();
            const encodedPath = encodeURIComponent(path);

            const card = document.createElement('div');
            card.className = 'image-card ' + category;
            card.dataset.path = path;

            const faceHtml = faceCount > 0 ? `<span class="face-count">${{faceCount}} faces</span>` : '';

            card.innerHTML = `
                <div class="image-container" onclick="openLightbox('${{encodedPath}}', '${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}')" >
                    <img data-src="/api/thumbnail?path=${{encodedPath}}" alt="${{fileName}}" class="lazy-image">
                </div>
                <div class="image-info">
                    <div class="image-path" title="${{path}}">${{fileName}}</div>
                    <div class="image-meta">
                        <span class="image-label" title="${{clipLabel}}">${{shortLabel}}</span>
                        ${{faceHtml}}
                        <button class="delete-btn" onclick="deleteImage('${{path.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'")}}',' ${{category}}', this)">Delete</button>
                    </div>
                </div>
            `;

            // Observe the image for lazy loading
            const img = card.querySelector('.lazy-image');
            imageObserver.observe(img);

            return card;
        }}

        function loadMore(category) {{
            const grid = document.getElementById(category + '-grid');
            const btn = document.getElementById(category + '-load-more');
            const items = data[category];
            const start = loaded[category];
            const end = Math.min(start + PAGE_SIZE, items.length);

            for (let i = start; i < end; i++) {{
                if (!deleted[category].has(items[i].path)) {{
                    grid.appendChild(createCard(items[i], category));
                }}
            }}

            loaded[category] = end;

            if (end >= items.length) {{
                btn.disabled = true;
                btn.textContent = 'All loaded';
            }} else {{
                btn.textContent = `Load More (${{items.length - end}} remaining)`;
            }}
        }}

        function showTab(tab) {{
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(tab + '-section').classList.add('active');
            event.target.classList.add('active');
            currentTab = tab;

            // Load first batch if not loaded
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
            setTimeout(() => toast.classList.remove('show'), 3000);
        }}

        function updateCounts() {{
            const counts = {{
                trash: data.trash.length - deleted.trash.size,
                review: data.review.length - deleted.review.size,
                keep: data.keep.length - deleted.keep.size
            }};
            document.getElementById('trash-count').textContent = counts.trash;
            document.getElementById('review-count').textContent = counts.review;
            document.getElementById('keep-count').textContent = counts.keep;
            document.getElementById('trash-tab-count').textContent = counts.trash;
            document.getElementById('review-tab-count').textContent = counts.review;
            document.getElementById('keep-tab-count').textContent = counts.keep;
        }}

        function deleteImage(path, category, button) {{
            if (!confirm('Delete this file?\\n\\n' + path)) return;

            button.disabled = true;
            button.textContent = '...';

            fetch('/api/delete', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path }}),
            }})
            .then(response => response.json())
            .then(result => {{
                if (result.success) {{
                    showToast('Deleted: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    card.classList.remove('keep', 'review', 'trash');
                    card.classList.add('deleted');
                    button.textContent = 'Deleted';
                    button.classList.add('deleted');
                    deleted[category.trim()].add(path);
                    updateCounts();
                }} else {{
                    showToast('Error: ' + result.error, 'error');
                    button.disabled = false;
                    button.textContent = 'Delete';
                }}
            }})
            .catch(error => {{
                showToast('Error: ' + error.message, 'error');
                button.disabled = false;
                button.textContent = 'Delete';
            }});
        }}

        function deleteAllVisible() {{
            const grid = document.getElementById(currentTab + '-grid');
            const cards = grid.querySelectorAll('.image-card:not(.deleted)');
            if (cards.length === 0) {{
                showToast('No images to delete', 'error');
                return;
            }}
            if (!confirm('Delete ' + cards.length + ' visible images?\\n\\nThis cannot be undone!')) return;

            cards.forEach(card => {{
                const btn = card.querySelector('.delete-btn:not(.deleted)');
                if (btn) {{
                    const path = card.dataset.path;
                    btn.disabled = true;
                    btn.textContent = '...';

                    fetch('/api/delete', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ path: path }}),
                    }})
                    .then(r => r.json())
                    .then(result => {{
                        if (result.success) {{
                            card.classList.remove('keep', 'review', 'trash');
                            card.classList.add('deleted');
                            btn.textContent = 'Deleted';
                            btn.classList.add('deleted');
                            deleted[currentTab].add(path);
                            updateCounts();
                        }}
                    }});
                }}
            }});

            showToast('Deleting ' + cards.length + ' files...', 'success');
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeLightbox();
        }});

        // Load initial batch for trash tab
        loadMore('trash');
    </script>
</body>
</html>
'''


def run_classify_server(report_path: Path, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the classification review server."""
    global _current_report, _report_path

    _report_path = report_path
    _current_report = load_report(report_path)

    app.run(host=host, port=port, debug=False, threaded=True)
