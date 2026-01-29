"""Web server for reviewing and deleting duplicate images."""

import json
import os
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string

from .review import generate_image_base64, THUMBNAIL_SIZE, LIGHTBOX_SIZE

app = Flask(__name__)

# Global state for current report
_current_report: dict | None = None
_report_path: Path | None = None


def load_report(report_path: Path) -> dict:
    """Load a JSON report file."""
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_report(report: dict, report_path: Path) -> None:
    """Save report back to JSON file."""
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def generate_server_html(report: dict) -> str:
    """Generate HTML for the server version with delete buttons."""
    html_parts = [_generate_html_header(report)]

    # Summary section
    html_parts.append(_generate_summary_section(report))

    # Exact duplicates section
    if report["exact_duplicates"]:
        html_parts.append('<h2 class="section-title exact">Exact Duplicates</h2>')
        for i, group in enumerate(report["exact_duplicates"], 1):
            html_parts.append(_generate_group_html(group, i, "exact"))

    # Similar images section
    if report["similar_images"]:
        html_parts.append('<h2 class="section-title similar">Similar Images</h2>')
        for i, group in enumerate(report["similar_images"], 1):
            html_parts.append(_generate_group_html(group, i, "similar"))

    # No duplicates message
    if not report["exact_duplicates"] and not report["similar_images"]:
        html_parts.append('''
            <div class="no-duplicates">
                <h2>No duplicates found!</h2>
                <p>Your images are all unique.</p>
            </div>
        ''')

    html_parts.append(_generate_html_footer())

    return "\n".join(html_parts)


def _generate_html_header(report: dict) -> str:
    """Generate the HTML header with CSS styles."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Image Dedup Review - {report.get("generated_at", "")}</title>
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
            color: #4ecca3;
        }}

        .summary-item .label {{
            color: #888;
            font-size: 0.9em;
        }}

        .section-title {{
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
            margin-top: 40px;
        }}

        .section-title.exact {{
            color: #e74c3c;
        }}

        .section-title.similar {{
            color: #f39c12;
        }}

        .group {{
            background: #16213e;
            border-radius: 12px;
            margin: 20px 0;
            padding: 20px;
            border-left: 4px solid #4ecca3;
        }}

        .group.exact {{
            border-left-color: #e74c3c;
        }}

        .group.similar {{
            border-left-color: #f39c12;
        }}

        .group-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .group-title {{
            font-size: 1.2em;
            font-weight: bold;
        }}

        .group-meta {{
            color: #888;
            font-size: 0.9em;
        }}

        .savings {{
            background: #4ecca3;
            color: #1a1a2e;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9em;
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
            width: 320px;
            transition: transform 0.2s;
        }}

        .image-card:hover {{
            transform: scale(1.02);
        }}

        .image-card.keep {{
            border: 3px solid #4ecca3;
        }}

        .image-card.duplicate {{
            border: 3px solid #e74c3c;
            opacity: 0.8;
        }}

        .image-card.deleted {{
            border: 3px solid #666;
            opacity: 0.4;
        }}

        .image-container {{
            width: 100%;
            height: 200px;
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
            font-size: 0.75em;
            color: #888;
            word-break: break-all;
            margin-bottom: 8px;
        }}

        .image-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }}

        .image-size {{
            font-weight: bold;
            color: #4ecca3;
        }}

        .image-badge {{
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: bold;
        }}

        .image-badge.keep {{
            background: #4ecca3;
            color: #1a1a2e;
        }}

        .image-badge.duplicate {{
            background: #e74c3c;
            color: #fff;
        }}

        .image-badge.deleted {{
            background: #666;
            color: #fff;
        }}

        .delete-btn {{
            background: #e74c3c;
            color: #fff;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.85em;
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

        .no-duplicates {{
            text-align: center;
            padding: 60px 20px;
            color: #4ecca3;
        }}

        .no-duplicates h2 {{
            font-size: 2em;
            margin-bottom: 10px;
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

        /* Lightbox modal */
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

        .lightbox-size {{
            font-size: 1.1em;
            color: #4ecca3;
            margin-top: 5px;
        }}

        .lightbox-close {{
            position: absolute;
            top: 20px;
            right: 30px;
            font-size: 40px;
            color: #fff;
            cursor: pointer;
            opacity: 0.7;
            transition: opacity 0.2s;
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

        /* Toast notification */
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

        .toast.show {{
            opacity: 1;
        }}

        .toast.success {{
            background: #4ecca3;
            color: #1a1a2e;
        }}

        .toast.error {{
            background: #e74c3c;
        }}
    </style>
</head>
<body>
    <!-- Lightbox modal -->
    <div id="lightbox" class="lightbox" onclick="closeLightbox()">
        <span class="lightbox-close">&times;</span>
        <img id="lightbox-img" src="" alt="">
        <div class="lightbox-info">
            <div id="lightbox-path" class="lightbox-path"></div>
            <div id="lightbox-size" class="lightbox-size"></div>
        </div>
        <div class="lightbox-hint">Click anywhere or press ESC to close</div>
    </div>

    <!-- Toast notification -->
    <div id="toast" class="toast"></div>

    <script>
        function openLightbox(imgSrc, path, size) {{
            document.getElementById('lightbox-img').src = imgSrc;
            document.getElementById('lightbox-path').textContent = path;
            document.getElementById('lightbox-size').textContent = size;
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
            setTimeout(() => {{
                toast.classList.remove('show');
            }}, 3000);
        }}

        function deleteImage(path, button) {{
            if (!confirm('Are you sure you want to delete this file?\\n\\n' + path)) {{
                return;
            }}

            button.disabled = true;
            button.textContent = 'Deleting...';

            fetch('/api/delete', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ path: path }}),
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    showToast('File deleted: ' + path.split('/').pop(), 'success');
                    // Mark the card as deleted
                    const card = button.closest('.image-card');
                    card.classList.remove('keep', 'duplicate');
                    card.classList.add('deleted');
                    // Update badge
                    const badge = card.querySelector('.image-badge');
                    badge.className = 'image-badge deleted';
                    badge.textContent = 'DELETED';
                    // Hide delete button
                    button.style.display = 'none';
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

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                closeLightbox();
            }}
        }});
    </script>
    <h1>Image Dedup Review</h1>
    <p class="subtitle">Generated: {report.get("generated_at", "Unknown")} | <strong>Server Mode</strong> - You can delete files</p>
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
            <div class="value">{summary.get("total_size_human", "0 B")}</div>
            <div class="label">Total Size</div>
        </div>
        <div class="summary-item">
            <div class="value">{summary.get("exact_duplicate_groups", 0)}</div>
            <div class="label">Exact Duplicate Groups</div>
        </div>
        <div class="summary-item">
            <div class="value">{summary.get("similar_groups", 0)}</div>
            <div class="label">Similar Image Groups</div>
        </div>
        <div class="summary-item">
            <div class="value">{summary.get("potential_savings_human", "0 B")}</div>
            <div class="label">Potential Savings</div>
        </div>
    </div>
    '''


def _generate_group_html(group: dict, index: int, group_type: str) -> str:
    """Generate HTML for a single group of duplicates."""
    files = group.get("files", [])
    savings = group.get("potential_savings_human", "0 B")
    similarity = group.get("similarity_bits")

    meta = ""
    if similarity is not None:
        meta = f'<span class="group-meta">{similarity} bits different</span>'

    images_html = []
    for i, file_info in enumerate(files):
        path = Path(file_info["path"])
        size = file_info.get("size_human", "Unknown")
        is_keep = i == 0  # First (largest) is marked as keep

        # Check if file exists
        file_exists = path.exists()

        # Generate thumbnail and lightbox images
        if file_exists:
            thumbnail = generate_image_base64(path, THUMBNAIL_SIZE)
            lightbox_img = generate_image_base64(path, LIGHTBOX_SIZE)
        else:
            thumbnail = None
            lightbox_img = None

        if thumbnail:
            # Escape quotes in path for JavaScript
            escaped_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
            lightbox_src = f"data:image/jpeg;base64,{lightbox_img}" if lightbox_img else f"data:image/jpeg;base64,{thumbnail}"
            img_html = f'<img src="data:image/jpeg;base64,{thumbnail}" alt="{path.name}" onclick="openLightbox(\'{lightbox_src}\', \'{escaped_path}\', \'{size}\')">'
        else:
            img_html = f'<span class="image-placeholder">{"File deleted" if not file_exists else "Cannot load image"}</span>'

        if not file_exists:
            badge_class = "deleted"
            badge_text = "DELETED"
            card_class = "deleted"
            delete_btn = ""
        else:
            badge_class = "keep" if is_keep else "duplicate"
            badge_text = "KEEP" if is_keep else "DUPLICATE"
            card_class = "keep" if is_keep else "duplicate"
            # Escape path for JavaScript
            js_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
            delete_btn = f'<button class="delete-btn" onclick="deleteImage(\'{js_path}\', this)">Delete</button>'

        images_html.append(f'''
            <div class="image-card {card_class}">
                <div class="image-container">
                    {img_html}
                </div>
                <div class="image-info">
                    <div class="image-path">{path}</div>
                    <div class="image-meta">
                        <span class="image-size">{size}</span>
                        <span class="image-badge {badge_class}">{badge_text}</span>
                        {delete_btn}
                    </div>
                </div>
            </div>
        ''')

    return f'''
    <div class="group {group_type}">
        <div class="group-header">
            <span class="group-title">Group {index}</span>
            {meta}
            <span class="savings">Save {savings}</span>
        </div>
        <div class="images-grid">
            {"".join(images_html)}
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
    """Serve the review HTML page."""
    global _current_report
    if _current_report is None:
        return "No report loaded", 500
    return generate_server_html(_current_report)


@app.route("/api/delete", methods=["POST"])
def delete_image():
    """Delete an image file."""
    global _current_report, _report_path

    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"success": False, "error": "No path provided"}), 400

    file_path = Path(data["path"])

    if not file_path.exists():
        return jsonify({"success": False, "error": "File not found"}), 404

    try:
        # Delete the file
        os.remove(file_path)
        return jsonify({"success": True, "message": f"Deleted {file_path}"})
    except PermissionError:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def run_server(report_path: Path, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the review server."""
    global _current_report, _report_path

    _report_path = report_path
    _current_report = load_report(report_path)

    app.run(host=host, port=port, debug=False)
