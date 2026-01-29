"""Command-line interface for image-dedup."""

import json
import os
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from .cache import HashCache
from .dedup import DeduplicationResult, DuplicateGroup, find_duplicates, format_size
from .review import generate_html_review
from .scanner import scan_multiple_directories
from .server import run_server

console = Console()


def print_group(group: DuplicateGroup, index: int) -> None:
    """Print a duplicate group."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("File", style="cyan")
    table.add_column("Size", justify="right")

    # Sort by size descending (largest first = likely best quality)
    sorted_images = sorted(group.images, key=lambda x: x.size, reverse=True)

    for i, img in enumerate(sorted_images):
        style = "green" if i == 0 else "dim"
        label = " (keep)" if i == 0 else " (duplicate)"
        table.add_row(
            str(img.path) + label,
            format_size(img.size),
            style=style
        )

    title = f"Group {index}"
    if group.match_type == "similar":
        title += f" (similarity: {group.similarity} bits different)"

    console.print(Panel(table, title=title, subtitle=f"Potential savings: {format_size(group.potential_savings)}"))


def run_scan(
    directories: tuple[Path, ...],
    recursive: bool,
    find_exact: bool,
    find_similar: bool,
    threshold: int,
    hash_size: int,
    output_json: bool,
    move_to: Path | None,
    dry_run: bool,
    use_cache: bool,
    cache_path: Path | None,
    no_report: bool,
) -> None:
    """Run the deduplication scan."""
    # Progress tracking
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Initializing...", total=None)

        def update_progress(status: str, current: int, total: int) -> None:
            progress.update(task, description=status, completed=current, total=total or None)

        result = find_duplicates(
            directories=list(directories),
            recursive=recursive,
            find_exact=find_exact,
            find_similar=find_similar,
            similarity_threshold=threshold,
            hash_size=hash_size,
            progress_callback=update_progress,
            use_cache=use_cache,
            cache_path=cache_path,
        )

    # Save JSON report by default
    report_path = None
    if not no_report:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = Path.cwd() / f"image-dedup-rep-{timestamp}.json"
        save_json_report(result, report_path)
        console.print(f"\n[green]Report saved to:[/green] {report_path}")

    if output_json:
        print_json_result(result)
        return

    # Print summary
    console.print()
    console.print(Panel(
        f"[bold]Scanned:[/bold] {result.total_images} images ({format_size(result.total_size)})\n"
        f"[bold]Exact duplicates:[/bold] {result.exact_duplicate_count} files in {len(result.exact_duplicates)} groups\n"
        f"[bold]Similar images:[/bold] {sum(len(g.images) for g in result.similar_images)} files in {len(result.similar_images)} groups\n"
        f"[bold]Potential savings:[/bold] {format_size(result.potential_savings_exact + result.potential_savings_similar)}\n"
        f"[bold]Errors:[/bold] {len(result.errors)} files could not be processed",
        title="Scan Results",
        border_style="blue"
    ))

    # Print exact duplicates
    if result.exact_duplicates:
        console.print()
        console.print("[bold red]Exact Duplicates:[/bold red]")
        for i, group in enumerate(result.exact_duplicates, 1):
            print_group(group, i)

    # Print similar images
    if result.similar_images:
        console.print()
        console.print("[bold yellow]Similar Images:[/bold yellow]")
        for i, group in enumerate(result.similar_images, 1):
            print_group(group, i)

    # Print errors if any
    if result.errors:
        console.print()
        console.print("[bold red]Errors:[/bold red]")
        for path, error in result.errors[:10]:  # Show first 10
            console.print(f"  {path}: {error}", style="dim red")
        if len(result.errors) > 10:
            console.print(f"  ... and {len(result.errors) - 10} more errors", style="dim")

    # Move duplicates if requested
    if move_to and (result.exact_duplicates or result.similar_images):
        move_duplicates(result, move_to, dry_run)


@click.group()
def main() -> None:
    """
    Find duplicate and similar images to free up disk space.

    Progress is automatically saved, so you can resume interrupted scans.
    Cache is stored in ~/.cache/image-dedup/cache.db
    """
    pass


@main.command()
@click.argument("directories", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--no-recursive", "-n", is_flag=True, help="Don't scan subdirectories")
@click.option("--exact-only", "-e", is_flag=True, help="Only find exact duplicates (faster)")
@click.option("--similar-only", "-s", is_flag=True, help="Only find similar images")
@click.option("--threshold", "-t", default=10, type=int, help="Similarity threshold (0-64, lower=stricter). Default: 10")
@click.option("--hash-size", default=16, type=int, help="Perceptual hash size (8, 16, 32). Default: 16")
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON")
@click.option("--move-to", "-m", type=click.Path(path_type=Path), help="Move duplicates to this directory")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be moved without actually moving")
@click.option("--no-cache", is_flag=True, help="Disable caching (don't save/resume progress)")
@click.option("--cache-path", type=click.Path(path_type=Path), help="Custom cache file path")
@click.option("--no-report", is_flag=True, help="Don't save JSON report file")
def scan(
    directories: tuple[Path, ...],
    no_recursive: bool,
    exact_only: bool,
    similar_only: bool,
    threshold: int,
    hash_size: int,
    output_json: bool,
    move_to: Path | None,
    dry_run: bool,
    no_cache: bool,
    cache_path: Path | None,
    no_report: bool,
) -> None:
    """
    Scan DIRECTORIES for duplicate and similar images.

    A JSON report is automatically saved to the current directory.

    Examples:

        image-dedup scan ~/Photos

        image-dedup scan ~/Photos ~/Downloads --threshold 5

        image-dedup scan ~/Photos --exact-only --move-to ~/Duplicates
    """
    run_scan(
        directories=directories,
        recursive=not no_recursive,
        find_exact=not similar_only,
        find_similar=not exact_only,
        threshold=threshold,
        hash_size=hash_size,
        output_json=output_json,
        move_to=move_to,
        dry_run=dry_run,
        use_cache=not no_cache,
        cache_path=cache_path,
        no_report=no_report,
    )


@main.group()
def cache() -> None:
    """Manage the hash cache."""
    pass


@cache.command()
def stats() -> None:
    """Show cache statistics."""
    with HashCache() as hc:
        s = hc.stats()

    console.print(Panel(
        f"[bold]Cache file:[/bold] {s['cache_path']}\n"
        f"[bold]Cache size:[/bold] {format_size(s['cache_size_bytes'])}\n"
        f"[bold]Total entries:[/bold] {s['total_entries']}\n"
        f"[bold]With SHA256:[/bold] {s['with_sha256']}\n"
        f"[bold]With pHash:[/bold] {s['with_phash']}",
        title="Cache Statistics",
        border_style="blue"
    ))


@cache.command()
@click.confirmation_option(prompt="Are you sure you want to clear the cache?")
def clear() -> None:
    """Clear all cached data."""
    with HashCache() as hc:
        count = hc.clear()

    console.print(f"[green]Cleared {count} cached entries.[/green]")


def move_duplicates(result: DeduplicationResult, destination: Path, dry_run: bool) -> None:
    """Move duplicate files to destination directory."""
    import shutil

    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    moved_size = 0

    all_groups = result.exact_duplicates + result.similar_images

    for group in all_groups:
        # Keep the largest file, move the rest
        sorted_images = sorted(group.images, key=lambda x: x.size, reverse=True)

        for img in sorted_images[1:]:  # Skip the first (largest) file
            dest_path = destination / img.path.name

            # Handle name conflicts
            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = destination / f"{stem}_{counter}{suffix}"
                    counter += 1

            if dry_run:
                console.print(f"  [dim]Would move:[/dim] {img.path} -> {dest_path}")
            else:
                try:
                    shutil.move(str(img.path), str(dest_path))
                    console.print(f"  [green]Moved:[/green] {img.path} -> {dest_path}")
                    moved_count += 1
                    moved_size += img.size
                except Exception as e:
                    console.print(f"  [red]Error moving {img.path}:[/red] {e}")

    if dry_run:
        console.print(f"\n[yellow]Dry run:[/yellow] Would move {moved_count} files")
    else:
        console.print(f"\n[green]Moved {moved_count} files ({format_size(moved_size)})[/green]")


def build_report_data(result: DeduplicationResult) -> dict:
    """Build the report data dictionary."""
    return {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_images": result.total_images,
            "total_size": result.total_size,
            "total_size_human": format_size(result.total_size),
            "exact_duplicate_count": result.exact_duplicate_count,
            "exact_duplicate_groups": len(result.exact_duplicates),
            "similar_count": sum(len(g.images) for g in result.similar_images),
            "similar_groups": len(result.similar_images),
            "potential_savings_exact": result.potential_savings_exact,
            "potential_savings_similar": result.potential_savings_similar,
            "potential_savings_total": result.potential_savings_exact + result.potential_savings_similar,
            "potential_savings_human": format_size(result.potential_savings_exact + result.potential_savings_similar),
            "error_count": len(result.errors),
        },
        "exact_duplicates": [
            {
                "files": [{"path": str(img.path), "size": img.size, "size_human": format_size(img.size)} for img in g.images],
                "potential_savings": g.potential_savings,
                "potential_savings_human": format_size(g.potential_savings),
            }
            for g in result.exact_duplicates
        ],
        "similar_images": [
            {
                "files": [{"path": str(img.path), "size": img.size, "size_human": format_size(img.size)} for img in g.images],
                "similarity_bits": g.similarity,
                "potential_savings": g.potential_savings,
                "potential_savings_human": format_size(g.potential_savings),
            }
            for g in result.similar_images
        ],
        "errors": [{"path": str(p), "error": e} for p, e in result.errors],
    }


def save_json_report(result: DeduplicationResult, path: Path) -> None:
    """Save results as JSON file."""
    data = build_report_data(result)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def print_json_result(result: DeduplicationResult) -> None:
    """Print results as JSON to stdout."""
    data = build_report_data(result)
    print(json.dumps(data, indent=2, ensure_ascii=False))


@main.command()
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output HTML file path")
@click.option("--open", "open_browser", is_flag=True, help="Open the HTML file in browser after generating")
def review(report: Path, output: Path | None, open_browser: bool) -> None:
    """
    Generate an HTML review page from a JSON report.

    Opens the report in your browser so you can visually verify duplicates
    before taking action.

    Examples:

        image-dedup review image-dedup-rep-20260129-164242.json

        image-dedup review report.json --open

        image-dedup review report.json -o my-review.html
    """
    console.print(f"[blue]Generating HTML review from:[/blue] {report}")

    try:
        output_path = generate_html_review(report, output)
        console.print(f"[green]Review page saved to:[/green] {output_path}")

        if open_browser:
            import webbrowser
            webbrowser.open(f"file://{output_path.absolute()}")
            console.print("[blue]Opened in browser[/blue]")

    except Exception as e:
        console.print(f"[red]Error generating review:[/red] {e}")
        raise SystemExit(1)


@main.command()
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--host", "-h", default="127.0.0.1", help="Host to bind to. Default: 127.0.0.1")
@click.option("--port", "-p", default=5000, type=int, help="Port to bind to. Default: 5000")
def serve(report: Path, host: str, port: int) -> None:
    """
    Start a web server to review and delete duplicates.

    Opens an interactive review page in your browser where you can
    visually verify duplicates and delete them directly.

    Examples:

        image-dedup serve image-dedup-rep-20260129-164242.json

        image-dedup serve report.json --port 8080

        image-dedup serve report.json --host 0.0.0.0
    """
    console.print(f"[blue]Loading report:[/blue] {report}")
    console.print(f"[green]Starting server at:[/green] http://{host}:{port}")
    console.print("[dim]Press Ctrl+C to stop the server[/dim]")

    try:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    except Exception:
        pass  # Don't fail if browser can't be opened

    try:
        run_server(report, host=host, port=port)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")
    except Exception as e:
        console.print(f"[red]Error starting server:[/red] {e}")
        raise SystemExit(1)


@main.command()
@click.argument("directories", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--no-recursive", "-n", is_flag=True, help="Don't scan subdirectories")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output JSON file path")
def classify(directories: tuple[Path, ...], no_recursive: bool, output: Path | None) -> None:
    """
    Classify images as family photos vs junk.

    Uses AI to detect:
    - Photos with faces (KEEP)
    - Real photos without faces (REVIEW)
    - Screenshots, memes, graphics (TRASH)

    Requires extra dependencies: pip install image-dedup[classify]

    Examples:

        image-dedup classify ~/Pictures

        image-dedup classify ~/Photos ~/Downloads -o classification.json
    """
    try:
        from .classifier import classify_images, Category
    except ImportError as e:
        console.print("[red]Classification dependencies not installed.[/red]")
        console.print("Run: [cyan]pip install image-dedup[classify][/cyan]")
        console.print(f"\nError: {e}")
        raise SystemExit(1)

    # Scan for images
    console.print("[blue]Scanning for images...[/blue]")
    image_paths = list(scan_multiple_directories(list(directories), recursive=not no_recursive))
    console.print(f"Found [green]{len(image_paths)}[/green] images")

    if not image_paths:
        console.print("[yellow]No images found.[/yellow]")
        return

    # Classify images
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Initializing AI models...", total=len(image_paths))

        def update_progress(status: str, current: int, total: int) -> None:
            progress.update(task, description=status, completed=current, total=total)

        report = classify_images(image_paths, progress_callback=update_progress)

    # Print summary
    console.print()
    console.print(Panel(
        f"[bold]Total images:[/bold] {report.total_images}\n"
        f"[bold green]KEEP (with faces):[/bold green] {report.keep_count}\n"
        f"[bold yellow]REVIEW (no faces):[/bold yellow] {report.review_count}\n"
        f"[bold red]TRASH (junk):[/bold red] {report.trash_count}\n"
        f"[bold]Errors:[/bold] {len(report.errors)}",
        title="Classification Results",
        border_style="blue"
    ))

    # Save JSON report
    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path.cwd() / f"image-classify-{timestamp}.json"

    # Get base directory from the first image path
    base_dir = ""
    if report.results:
        first_path = report.results[0].path
        base_dir = str(first_path.parent)
        # Try to find common parent for all paths
        all_parents = [str(r.path.parent) for r in report.results]
        common = os.path.commonpath(all_parents) if all_parents else ""
        if common:
            base_dir = common

    report_data = {
        "generated_at": datetime.now().isoformat(),
        "base_directory": base_dir,
        "summary": {
            "total_images": report.total_images,
            "keep_count": report.keep_count,
            "review_count": report.review_count,
            "trash_count": report.trash_count,
            "error_count": len(report.errors),
        },
        "keep": [
            {
                "path": str(r.path),
                "face_count": r.face_count,
                "clip_label": r.best_clip_label,
                "confidence": r.confidence,
            }
            for r in report.results if r.category == Category.KEEP
        ],
        "review": [
            {
                "path": str(r.path),
                "clip_label": r.best_clip_label,
                "confidence": r.confidence,
            }
            for r in report.results if r.category == Category.REVIEW
        ],
        "trash": [
            {
                "path": str(r.path),
                "clip_label": r.best_clip_label,
                "confidence": r.confidence,
            }
            for r in report.results if r.category == Category.TRASH
        ],
        "errors": [{"path": str(p), "error": e} for p, e in report.errors],
    }

    with open(output, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]Report saved to:[/green] {output}")
    console.print("\n[dim]Use 'image-dedup classify-review <report.json>' to review results[/dim]")


@main.command("classify-review")
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--host", "-h", default="127.0.0.1", help="Host to bind to. Default: 127.0.0.1")
@click.option("--port", "-p", default=5000, type=int, help="Port to bind to. Default: 5000")
@click.option("--https", "use_https", is_flag=True, help="Use HTTPS (required for clipboard/WhatsApp sharing)")
def classify_review(report: Path, host: str, port: int, use_https: bool) -> None:
    """
    Start a web server to review classification results.

    Opens an interactive page to review KEEP/REVIEW/TRASH images
    and delete the ones you don't want.

    Use --https to enable clipboard functionality (needed for WhatsApp sharing).

    Examples:

        image-dedup classify-review image-classify-20260129-164242.json

        image-dedup classify-review report.json --https --host 0.0.0.0
    """
    from .classify_server import run_classify_server

    protocol = "https" if use_https else "http"
    console.print(f"[blue]Loading report:[/blue] {report}")
    console.print(f"[green]Starting server at:[/green] {protocol}://{host}:{port}")
    if use_https:
        console.print("[yellow]HTTPS enabled - browser will show security warning, click 'Advanced' → 'Proceed'[/yellow]")
    console.print("[dim]Press Ctrl+C to stop the server[/dim]")

    try:
        import webbrowser
        webbrowser.open(f"{protocol}://{host}:{port}")
    except Exception:
        pass

    try:
        run_classify_server(report, host=host, port=port, use_https=use_https)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")
    except Exception as e:
        console.print(f"[red]Error starting server:[/red] {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
