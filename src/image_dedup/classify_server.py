"""Web server for reviewing and deleting classified images."""

import json
import os
from pathlib import Path

from flask import Flask, jsonify, request

from .review import generate_image_base64, THUMBNAIL_SIZE, LIGHTBOX_SIZE

app = Flask(__name__)

# Global state
_current_report: dict | None = None
_report_path: Path | None = None


def load_report(report_path: Path) -> dict:
    """Load a JSON report file."""
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_classify_html(report: dict) -> str:
    """Generate HTML for classification review."""
    html_parts = [_generate_html_header(report)]

    # Summary section
    html_parts.append(_generate_summary_section(report))

    # Tab navigation
    html_parts.append('''
    <div class="tabs">
        <button class="tab active" onclick="showTab('trash')">TRASH <span class="count trash-count"></span></button>
        <button class="tab" onclick="showTab('review')">REVIEW <span class="count review-count"></span></button>
        <button class="tab" onclick="showTab('keep')">KEEP <span class="count keep-count"></span></button>
    </div>
    ''')

    # TRASH section (shown by default - most likely to delete)
    html_parts.append('<div id="trash-section" class="section active">')
    html_parts.append('<h2 class="section-title trash">TRASH - Probably Delete</h2>')
    html_parts.append('<p class="section-desc">Screenshots, memes, graphics - probably safe to delete</p>')
    html_parts.append('<div class="images-grid">')
    for item in report.get("trash", []):
        html_parts.append(_generate_image_card(item, "trash"))
    if not report.get("trash"):
        html_parts.append('<p class="empty">No trash images found</p>')
    html_parts.append('</div></div>')

    # REVIEW section
    html_parts.append('<div id="review-section" class="section">')
    html_parts.append('<h2 class="section-title review">REVIEW - Check Manually</h2>')
    html_parts.append('<p class="section-desc">Real photos without faces - review before deleting</p>')
    html_parts.append('<div class="images-grid">')
    for item in report.get("review", []):
        html_parts.append(_generate_image_card(item, "review"))
    if not report.get("review"):
        html_parts.append('<p class="empty">No images to review</p>')
    html_parts.append('</div></div>')

    # KEEP section
    html_parts.append('<div id="keep-section" class="section">')
    html_parts.append('<h2 class="section-title keep">KEEP - Family Photos</h2>')
    html_parts.append('<p class="section-desc">Photos with faces detected - probably want to keep</p>')
    html_parts.append('<div class="images-grid">')
    for item in report.get("keep", []):
        html_parts.append(_generate_image_card(item, "keep"))
    if not report.get("keep"):
        html_parts.append('<p class="empty">No family photos found</p>')
    html_parts.append('</div></div>')

    html_parts.append(_generate_html_footer())

    return "\n".join(html_parts)


def _generate_html_header(report: dict) -> str:
    """Generate the HTML header with CSS styles."""
    summary = report.get("summary", {})
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Image Classification Review</title>
    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
            background: #1a1a2e;
            color: #eee;
            margin: 0;
            padding: 20px;
            line-height: 1.6;
        }}

        h1 {{
            text-align: center;
            color: #fff;
            margin-bottom: 10px;
        }}

        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 30px;
        }}

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

        .summary-item {{
            text-align: center;
        }}

        .summary-item .value {{
            font-size: 2em;
            font-weight: bold;
        }}

        .summary-item .value.keep {{ color: #4ecca3; }}
        .summary-item .value.review {{ color: #f39c12; }}
        .summary-item .value.trash {{ color: #e74c3c; }}

        .summary-item .label {{
            color: #888;
            font-size: 0.9em;
        }}

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

        .tab:hover {{
            background: #1f2b4a;
        }}

        .tab.active {{
            background: #4ecca3;
            color: #1a1a2e;
        }}

        .tab .count {{
            margin-left: 8px;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.85em;
        }}

        .section {{
            display: none;
        }}

        .section.active {{
            display: block;
        }}

        .section-title {{
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
            margin-top: 20px;
        }}

        .section-title.keep {{ color: #4ecca3; }}
        .section-title.review {{ color: #f39c12; }}
        .section-title.trash {{ color: #e74c3c; }}

        .section-desc {{
            color: #888;
            margin-bottom: 20px;
        }}

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

        .image-card:hover {{
            transform: scale(1.02);
        }}

        .image-card.keep {{
            border: 3px solid #4ecca3;
        }}

        .image-card.review {{
            border: 3px solid #f39c12;
        }}

        .image-card.trash {{
            border: 3px solid #e74c3c;
        }}

        .image-card.deleted {{
            border: 3px solid #666;
            opacity: 0.4;
        }}

        .image-container {{
            width: 100%;
            height: 180px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
            cursor: pointer;
        }}

        .image-container:hover {{
            opacity: 0.9;
        }}

        .image-container img {{
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }}

        .image-placeholder {{
            color: #666;
            font-size: 0.9em;
        }}

        .image-info {{
            padding: 12px;
        }}

        .image-path {{
            font-size: 0.7em;
            color: #888;
            word-break: break-all;
            margin-bottom: 6px;
            max-height: 2.8em;
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
            font-size: 0.75em;
            color: #aaa;
            max-width: 150px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .face-count {{
            background: #4ecca3;
            color: #1a1a2e;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: bold;
        }}

        .delete-btn {{
            background: #e74c3c;
            color: #fff;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.8em;
            font-weight: bold;
            transition: background 0.2s;
        }}

        .delete-btn:hover {{
            background: #c0392b;
        }}

        .delete-btn:disabled {{
            background: #666;
            cursor: not-allowed;
        }}

        .delete-btn.deleted {{
            background: #666;
        }}

        .empty {{
            color: #666;
            font-style: italic;
            padding: 40px;
            text-align: center;
            width: 100%;
        }}

        footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #333;
            color: #666;
        }}

        footer a {{
            color: #4ecca3;
            text-decoration: none;
        }}

        /* Lightbox */
        .lightbox {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
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

        .lightbox img {{
            max-width: 90%;
            max-height: 80%;
            object-fit: contain;
            border-radius: 8px;
        }}

        .lightbox-info {{
            color: #fff;
            margin-top: 15px;
            text-align: center;
            max-width: 90%;
        }}

        .lightbox-path {{
            font-size: 0.9em;
            color: #888;
            word-break: break-all;
        }}

        .lightbox-close {{
            position: absolute;
            top: 20px;
            right: 30px;
            font-size: 40px;
            color: #fff;
            cursor: pointer;
            opacity: 0.7;
        }}

        .lightbox-close:hover {{
            opacity: 1;
        }}

        .lightbox-hint {{
            position: absolute;
            bottom: 20px;
            color: #666;
            font-size: 0.9em;
        }}

        /* Toast */
        .toast {{
            position: fixed;
            bottom: 20px;
            right: 20px;
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

        /* Bulk actions */
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
            transition: background 0.2s;
        }}

        .bulk-btn:hover {{
            background: #c0392b;
        }}

        .bulk-btn:disabled {{
            background: #666;
            cursor: not-allowed;
        }}
    </style>
</head>
<body>
    <!-- Lightbox -->
    <div id="lightbox" class="lightbox" onclick="closeLightbox()">
        <span class="lightbox-close">&times;</span>
        <img id="lightbox-img" src="" alt="">
        <div class="lightbox-info">
            <div id="lightbox-path" class="lightbox-path"></div>
        </div>
        <div class="lightbox-hint">Click anywhere or press ESC to close</div>
    </div>

    <!-- Toast -->
    <div id="toast" class="toast"></div>

    <script>
        let trashCount = {summary.get("trash_count", 0)};
        let reviewCount = {summary.get("review_count", 0)};
        let keepCount = {summary.get("keep_count", 0)};

        function updateCounts() {{
            document.querySelector('.trash-count').textContent = trashCount;
            document.querySelector('.review-count').textContent = reviewCount;
            document.querySelector('.keep-count').textContent = keepCount;
        }}
        updateCounts();

        function showTab(tab) {{
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(tab + '-section').classList.add('active');
            event.target.classList.add('active');
        }}

        function openLightbox(imgSrc, path) {{
            document.getElementById('lightbox-img').src = imgSrc;
            document.getElementById('lightbox-path').textContent = path;
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

        function deleteImage(path, button, category) {{
            if (!confirm('Delete this file?\\n\\n' + path)) return;

            button.disabled = true;
            button.textContent = 'Deleting...';

            fetch('/api/delete', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ path: path }}),
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    showToast('Deleted: ' + path.split(/[\\\\/]/).pop(), 'success');
                    const card = button.closest('.image-card');
                    card.classList.remove('keep', 'review', 'trash');
                    card.classList.add('deleted');
                    button.textContent = 'Deleted';
                    button.classList.add('deleted');

                    // Update counts
                    if (category === 'trash') trashCount--;
                    else if (category === 'review') reviewCount--;
                    else if (category === 'keep') keepCount--;
                    updateCounts();
                }} else {{
                    showToast('Error: ' + data.error, 'error');
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

        function deleteAllTrash() {{
            const cards = document.querySelectorAll('#trash-section .image-card:not(.deleted)');
            if (cards.length === 0) {{
                showToast('No trash images to delete', 'error');
                return;
            }}
            if (!confirm('Delete ALL ' + cards.length + ' trash images?\\n\\nThis cannot be undone!')) return;

            cards.forEach(card => {{
                const btn = card.querySelector('.delete-btn');
                if (btn && !btn.disabled) {{
                    const path = btn.getAttribute('data-path');
                    deleteImage(path, btn, 'trash');
                }}
            }});
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeLightbox();
        }});
    </script>

    <h1>Image Classification Review</h1>
    <p class="subtitle">AI-powered classification | Server Mode - You can delete files</p>
'''


def _generate_summary_section(report: dict) -> str:
    """Generate the summary section HTML."""
    summary = report.get("summary", {})
    return f'''
    <div class="summary">
        <div class="summary-item">
            <div class="value">{summary.get("total_images", 0)}</div>
            <div class="label">Total Images</div>
        </div>
        <div class="summary-item">
            <div class="value keep">{summary.get("keep_count", 0)}</div>
            <div class="label">KEEP (Faces)</div>
        </div>
        <div class="summary-item">
            <div class="value review">{summary.get("review_count", 0)}</div>
            <div class="label">REVIEW</div>
        </div>
        <div class="summary-item">
            <div class="value trash">{summary.get("trash_count", 0)}</div>
            <div class="label">TRASH</div>
        </div>
    </div>

    <div class="bulk-actions">
        <button class="bulk-btn" onclick="deleteAllTrash()">Delete All Trash</button>
        <span style="color: #888;">Bulk delete all images classified as trash</span>
    </div>
    '''


def _generate_image_card(item: dict, category: str) -> str:
    """Generate HTML for a single image card."""
    path = Path(item["path"])
    clip_label = item.get("clip_label", "unknown")
    face_count = item.get("face_count", 0)

    # Check if file exists
    file_exists = path.exists()

    # Generate thumbnail
    if file_exists:
        thumbnail = generate_image_base64(path, THUMBNAIL_SIZE)
        lightbox_img = generate_image_base64(path, LIGHTBOX_SIZE)
    else:
        thumbnail = None
        lightbox_img = None

    if thumbnail:
        escaped_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
        lightbox_src = f"data:image/jpeg;base64,{lightbox_img}" if lightbox_img else f"data:image/jpeg;base64,{thumbnail}"
        img_html = f'<img src="data:image/jpeg;base64,{thumbnail}" alt="{path.name}" onclick="openLightbox(\'{lightbox_src}\', \'{escaped_path}\')">'
    else:
        img_html = f'<span class="image-placeholder">{"File deleted" if not file_exists else "Cannot load"}</span>'

    if not file_exists:
        card_class = "deleted"
        delete_btn = '<button class="delete-btn deleted" disabled>Deleted</button>'
    else:
        card_class = category
        js_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
        delete_btn = f'<button class="delete-btn" data-path="{path}" onclick="deleteImage(\'{js_path}\', this, \'{category}\')">Delete</button>'

    face_html = f'<span class="face-count">{face_count} faces</span>' if face_count > 0 else ''

    # Shorten clip label for display
    short_label = clip_label.replace("a ", "").replace("photograph of ", "")[:25]

    return f'''
        <div class="image-card {card_class}">
            <div class="image-container">
                {img_html}
            </div>
            <div class="image-info">
                <div class="image-path" title="{path}">{path.name}</div>
                <div class="image-meta">
                    <span class="image-label" title="{clip_label}">{short_label}</span>
                    {face_html}
                    {delete_btn}
                </div>
            </div>
        </div>
    '''


def _generate_html_footer() -> str:
    """Generate the HTML footer."""
    return '''
    <footer>
        Generated by <a href="https://github.com/fpelliccioni/image-dedup">image-dedup</a>
    </footer>
</body>
</html>
'''


@app.route("/")
def index():
    """Serve the classification review page."""
    global _current_report
    if _current_report is None:
        return "No report loaded", 500
    return generate_classify_html(_current_report)


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


def run_classify_server(report_path: Path, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the classification review server."""
    global _current_report, _report_path

    _report_path = report_path
    _current_report = load_report(report_path)

    app.run(host=host, port=port, debug=False)
