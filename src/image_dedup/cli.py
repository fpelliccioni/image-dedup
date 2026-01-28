"""Command-line interface for image-dedup."""

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from .dedup import DeduplicationResult, DuplicateGroup, find_duplicates, format_size

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


@click.command()
@click.argument("directories", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--no-recursive", "-n", is_flag=True, help="Don't scan subdirectories")
@click.option("--exact-only", "-e", is_flag=True, help="Only find exact duplicates (faster)")
@click.option("--similar-only", "-s", is_flag=True, help="Only find similar images")
@click.option("--threshold", "-t", default=10, type=int, help="Similarity threshold (0-64, lower=stricter). Default: 10")
@click.option("--hash-size", "-h", default=16, type=int, help="Perceptual hash size (8, 16, 32). Larger=more precise. Default: 16")
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON")
@click.option("--move-to", "-m", type=click.Path(path_type=Path), help="Move duplicates to this directory")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be moved without actually moving")
def main(
    directories: tuple[Path, ...],
    no_recursive: bool,
    exact_only: bool,
    similar_only: bool,
    threshold: int,
    hash_size: int,
    output_json: bool,
    move_to: Path | None,
    dry_run: bool,
) -> None:
    """
    Find duplicate and similar images in DIRECTORIES.

    Examples:

        image-dedup ~/Photos

        image-dedup ~/Photos ~/Downloads --threshold 5

        image-dedup ~/Photos --exact-only --move-to ~/Duplicates
    """
    find_exact = not similar_only
    find_similar = not exact_only

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
            recursive=not no_recursive,
            find_exact=find_exact,
            find_similar=find_similar,
            similarity_threshold=threshold,
            hash_size=hash_size,
            progress_callback=update_progress,
        )

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


def print_json_result(result: DeduplicationResult) -> None:
    """Print results as JSON."""
    import json

    data = {
        "summary": {
            "total_images": result.total_images,
            "total_size": result.total_size,
            "exact_duplicate_count": result.exact_duplicate_count,
            "similar_count": result.similar_count,
            "potential_savings_exact": result.potential_savings_exact,
            "potential_savings_similar": result.potential_savings_similar,
            "error_count": len(result.errors),
        },
        "exact_duplicates": [
            {
                "files": [{"path": str(img.path), "size": img.size} for img in g.images],
                "potential_savings": g.potential_savings,
            }
            for g in result.exact_duplicates
        ],
        "similar_images": [
            {
                "files": [{"path": str(img.path), "size": img.size} for img in g.images],
                "similarity": g.similarity,
                "potential_savings": g.potential_savings,
            }
            for g in result.similar_images
        ],
        "errors": [{"path": str(p), "error": e} for p, e in result.errors],
    }

    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
