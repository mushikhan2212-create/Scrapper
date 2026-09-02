"""Command line interface."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .config import PROJECT_ROOT, Settings
from .http import Fetcher
from .models import Vehicle
from .pipeline import run_scrape
from .sources.base import Target
from .sources.registry import SOURCES, get_source
from .storage import iter_ndjson

app = typer.Typer(add_completion=False, help="Scrape used-car listings into JSON.")
console = Console()

DEFAULT_TARGETS_FILE = PROJECT_ROOT / "targets.yml"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_targets(path: Path, source_name: str) -> list[Target]:
    """Read targets.yml into Target objects."""
    if not path.exists():
        raise typer.BadParameter(f"targets file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = (data.get("sources") or {}).get(source_name) or []
    targets: list[Target] = []
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        targets.append(
            Target(
                make=entry["make"],
                model=entry.get("model"),
                max_pages=int(entry.get("max_pages", 1)),
                filters={k: str(v) for k, v in (entry.get("filters") or {}).items()},
            )
        )
    return targets


@app.command()
def scrape(
    target: list[str] = typer.Option(
        None, "--target", "-t", help="make:model, repeatable. Overrides targets.yml."
    ),
    source: str = typer.Option("beforward", "--source", "-s", help="Provider to scrape."),
    all_targets: bool = typer.Option(False, "--all", help="Scrape every target in targets.yml."),
    max_pages: int = typer.Option(1, "--max-pages", help="Results pages per target."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Cap on vehicles scraped."),
    concurrency: Optional[int] = typer.Option(None, "--concurrency"),
    delay: Optional[float] = typer.Option(None, "--delay", help="Seconds between requests."),
    cache: bool = typer.Option(False, "--cache", help="Reuse cached HTML (dev only)."),
    ignore_robots: bool = typer.Option(False, "--ignore-robots", hidden=True),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover URLs, fetch no details."),
    targets_file: Path = typer.Option(DEFAULT_TARGETS_FILE, "--targets-file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scrape listings and write NDJSON + JSON into data/<source>/<run>/."""
    _configure_logging(verbose)

    if target:
        targets = [Target.parse(spec, max_pages=max_pages) for spec in target]
    elif all_targets:
        targets = _load_targets(targets_file, source)
    else:
        raise typer.BadParameter("pass --target make:model, or --all to use targets.yml")

    if not targets:
        raise typer.BadParameter(f"no enabled targets for source {source!r}")

    settings = Settings(use_cache=cache, respect_robots=not ignore_robots)
    if concurrency is not None:
        settings.concurrency = concurrency
    if delay is not None:
        settings.delay_seconds = delay

    console.print(
        f"[bold]{source}[/bold] — {len(targets)} target(s): "
        f"{', '.join(t.key for t in targets)}"
    )

    manifest = asyncio.run(
        run_scrape(get_source(source), targets, settings, limit=limit, dry_run=dry_run)
    )

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] discovered {manifest['discovered']} listings")
        for url in manifest["sample_urls"]:
            console.print(f"  {url}")
        return

    _print_manifest(manifest, settings.run_dir(source, manifest["run_id"]))


def _print_manifest(manifest: dict, run_dir: Path) -> None:
    table = Table(show_header=False, box=None)
    table.add_row("Run", manifest["run_id"])
    table.add_row("Vehicles", str(manifest["vehicles_written"]))
    table.add_row("Errors", str(manifest["errors"]))
    table.add_row("Requests", str(manifest["http"].get("requests", 0)))
    table.add_row("Duration", f"{manifest['duration_seconds']}s")
    table.add_row("Output", str(run_dir))
    console.print(table)
    if manifest["errors"]:
        console.print(f"[yellow]{manifest['errors']} failures logged to errors.ndjson[/yellow]")


@app.command()
def capture(
    url: str = typer.Argument(..., help="Page to save."),
    name: str = typer.Option(..., "--name", "-n", help="Fixture name, no extension."),
    source: str = typer.Option("beforward", "--source", "-s"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Save a page's HTML as a test fixture.

    Run this once against a real listing page and a real detail page; commit the
    results so the parsers can be developed and regression-tested offline.
    """
    _configure_logging(verbose)
    settings = Settings()
    fixtures_dir = PROJECT_ROOT / "src" / "scrapper" / "sources" / source / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    destination = fixtures_dir / f"{name}.html"

    async def _fetch() -> str:
        async with Fetcher(settings) as fetcher:
            return (await fetcher.get(url)).text

    html = asyncio.run(_fetch())
    destination.write_text(html, encoding="utf-8")
    console.print(f"Saved [bold]{len(html):,}[/bold] bytes to {destination}")


@app.command()
def inspect(
    fixture: Path = typer.Argument(..., help="Saved HTML file to parse."),
    url: str = typer.Option("https://www.beforward.jp/stocklist/detail/1234567", "--url"),
    source: str = typer.Option("beforward", "--source", "-s"),
    listing: bool = typer.Option(False, "--listing", help="Parse as a results page."),
) -> None:
    """Parse a saved fixture and print the result. The selector-fixing loop."""
    impl = get_source(source)
    html = fixture.read_text(encoding="utf-8")
    if listing:
        page = impl.parse_listing(html, url)
        console.print(f"total_pages={page.total_pages} total_results={page.total_results}")
        console.print(f"items={len(page.items)}")
        for item in page.items[:10]:
            console.print(f"  {item.source_id}  {item.detail_url}")
    else:
        console.print_json(impl.parse_detail(html, url).model_dump_json())


@app.command()
def validate(
    path: Path = typer.Argument(..., help="vehicles.ndjson to check."),
) -> None:
    """Re-validate every record in an NDJSON file against the schema."""
    ok = 0
    failures: list[str] = []
    for index, record in enumerate(iter_ndjson(path), start=1):
        try:
            Vehicle.model_validate(record)
            ok += 1
        except Exception as exc:
            failures.append(f"line {index}: {exc}")

    console.print(f"[green]{ok} valid[/green]  [red]{len(failures)} invalid[/red]")
    for failure in failures[:10]:
        console.print(f"  {failure}")
    if failures:
        raise typer.Exit(code=1)


@app.command()
def stats(path: Path = typer.Argument(..., help="vehicles.ndjson to summarize.")) -> None:
    """Field coverage report — which fields the parser is actually filling."""
    records = list(iter_ndjson(path))
    if not records:
        console.print("[yellow]no records[/yellow]")
        raise typer.Exit(code=1)

    counts: dict[str, int] = {}
    for record in records:
        for key, value in record.items():
            if value not in (None, [], {}, ""):
                counts[key] = counts.get(key, 0) + 1

    table = Table("Field", "Populated", "Coverage")
    total = len(records)
    for key in sorted(counts, key=lambda k: -counts[k]):
        table.add_row(key, f"{counts[key]}/{total}", f"{counts[key] / total:.0%}")
    console.print(table)


@app.command("sources")
def list_sources() -> None:
    """List available providers."""
    for name in sorted(SOURCES):
        console.print(f"  {name}")


if __name__ == "__main__":
    app()
