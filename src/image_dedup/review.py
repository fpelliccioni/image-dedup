"""Generate HTML review page from JSON report."""

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

THUMBNAIL_SIZE = (300, 300)
LIGHTBOX_SIZE = (1200, 1200)


def generate_image_base64(image_path: Path, size: tuple[int, int]) -> str | None:
    """Generate a base64-encoded thumbnail of an image."""
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (for PNG with transparency, etc.)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.thumbnail(size, Image.Resampling.LANCZOS)

            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            return base64.b64encode(buffer.read()).decode("utf-8")
    except Exception:
        return None


def generate_html_review(report_path: Path, output_path: Path | None = None) -> Path:
    """
    Generate an HTML review page from a JSON report.

    Args:
        report_path: Path to the JSON report file
        output_path: Output path for HTML file. If None, uses report name with .html extension

    Returns:
        Path to the generated HTML file
    """
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    if output_path is None:
        output_path = report_path.with_suffix(".html")

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

    html_content = "\n".join(html_parts)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


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

        .image-container {{
            width: 100%;
            height: 200px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
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

        .image-container {{
            cursor: pointer;
        }}

        .image-container:hover {{
            opacity: 0.9;
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

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                closeLightbox();
            }}
        }});
    </script>
    <h1>Image Dedup Review</h1>
    <p class="subtitle">Generated: {report.get("generated_at", "Unknown")}</p>
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

        # Generate thumbnail and lightbox images
        thumbnail = generate_image_base64(path, THUMBNAIL_SIZE)
        lightbox_img = generate_image_base64(path, LIGHTBOX_SIZE)

        if thumbnail:
            # Escape quotes in path for JavaScript
            escaped_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
            lightbox_src = f"data:image/jpeg;base64,{lightbox_img}" if lightbox_img else f"data:image/jpeg;base64,{thumbnail}"
            img_html = f'<img src="data:image/jpeg;base64,{thumbnail}" alt="{path.name}" onclick="openLightbox(\'{lightbox_src}\', \'{escaped_path}\', \'{size}\')">'
        else:
            img_html = f'<span class="image-placeholder">Cannot load image</span>'

        badge_class = "keep" if is_keep else "duplicate"
        badge_text = "KEEP" if is_keep else "DUPLICATE"
        card_class = "keep" if is_keep else "duplicate"

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
