"""End-to-end pipeline test against a local HTTP server.

beforward.jp is unreachable from CI/dev containers behind an egress policy, so
the whole chain — fetch, paginate, dedupe, parse, write NDJSON/JSON/manifest,
resume — is exercised against a local server serving the synthetic fixtures.
This is what proves the plumbing works before anyone points it at the real site.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scrapper.config import Settings
from scrapper.pipeline import run_scrape
from scrapper.sources.base import Target
from scrapper.sources.beforward.source import BeForwardSource, extract_stock_id
from scrapper.storage import iter_ndjson

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


class _Handler(BaseHTTPRequestHandler):
    """Serves the listing on any /stocklist/ query, and details by stock id."""

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.startswith("/robots.txt"):
            self.send_response(404)
            self.end_headers()
            return

        # Resolve detail pages through the same id extraction the scraper uses,
        # so every URL form the listing emits is served.
        stock_id = extract_stock_id(self.path)
        if stock_id is not None:
            name = "detail_jsonld.html" if stock_id == "8811001" else "detail_no_jsonld.html"
            body = FIXTURES.joinpath(name).read_text(encoding="utf-8")
            # Rewrite the id so each detail page is a distinct vehicle.
            body = body.replace("8811001", stock_id).replace("8811002", stock_id)
        elif self.path.startswith("/stocklist"):
            body = FIXTURES.joinpath("listing.html").read_text(encoding="utf-8")
        else:
            self.send_response(404)
            self.end_headers()
            return

        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: object) -> None:
        pass  # Keep pytest output clean.


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def local_source(server):
    """BeForwardSource pointed at the local server instead of the real site."""

    class LocalBeForward(BeForwardSource):
        base_url = server

    return LocalBeForward()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        output_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        concurrency=4,
        delay_seconds=0.0,
        jitter_seconds=0.0,
        respect_robots=True,
    )


async def test_full_run_writes_all_expected_outputs(local_source, settings):
    manifest = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="testrun"
    )

    run_dir = settings.run_dir("beforward", "testrun")
    assert manifest["vehicles_written"] == 3
    assert manifest["errors"] == 0

    # All four output files exist.
    for name in ("vehicles.ndjson", "vehicles.json", "manifest.json", "seen.txt"):
        assert (run_dir / name).exists(), f"{name} missing"

    # The internal NDJSON and the exported envelope agree.
    ndjson_records = list(iter_ndjson(run_dir / "vehicles.ndjson"))
    envelope = json.loads((run_dir / "vehicles.json").read_text(encoding="utf-8"))

    assert set(envelope) == {"sourceCode", "capturedAtUtc", "vehicles"}
    assert envelope["sourceCode"] == "beforward"
    assert len(ndjson_records) == len(envelope["vehicles"]) == 3
    assert {r["source_id"] for r in ndjson_records} == {"8811001", "8811002", "8811003"}
    assert {v["externalId"] for v in envelope["vehicles"]} == {"8811001", "8811002", "8811003"}


async def test_export_envelope_carries_configured_markets(local_source, settings):
    """Markets are a run-level config value stamped onto every exported vehicle."""
    await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings,
        run_id="mkt", markets=["PK", "KE"],
    )
    envelope = json.loads(
        (settings.run_dir("beforward", "mkt") / "vehicles.json").read_text(encoding="utf-8")
    )
    assert all(v["destinationMarkets"] == ["PK", "KE"] for v in envelope["vehicles"])


async def test_records_are_correctly_typed(local_source, settings):
    """The output is the deliverable — check it is import-ready, not just present."""
    await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="typed"
    )
    records = {
        r["source_id"]: r
        for r in iter_ndjson(settings.run_dir("beforward", "typed") / "vehicles.ndjson")
    }

    car = records["8811001"]
    assert car["make"] == "Toyota"
    assert isinstance(car["year"], int) and car["year"] == 2015
    assert isinstance(car["mileage_km"], int) and car["mileage_km"] == 78000
    assert car["price"]["fob_amount"] == 4850.0 and car["price"]["currency"] == "USD"
    assert car["images"] and all(u.startswith("http") for u in car["images"])
    assert car["content_hash"] and car["scraped_at"]
    assert car["source_url"].endswith("/detail/8811001")


async def test_manifest_reports_the_run(local_source, settings):
    manifest = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="man"
    )
    on_disk = json.loads(
        (settings.run_dir("beforward", "man") / "manifest.json").read_text(encoding="utf-8")
    )
    assert on_disk["vehicles_written"] == 3
    assert on_disk["source"] == "beforward"
    assert on_disk["targets"] == ["Toyota:Corolla Axio"]
    assert on_disk["http"]["requests"] >= 4  # 1 listing + 3 details
    assert on_disk["vehicles_total_in_file"] == 3
    assert manifest["discovered"] == 3


async def test_limit_caps_the_run(local_source, settings):
    manifest = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings,
        limit=2, run_id="limited",
    )
    assert manifest["vehicles_written"] == 2


async def test_dry_run_fetches_no_detail_pages(local_source, settings):
    manifest = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings,
        dry_run=True, run_id="dry",
    )
    assert manifest["dry_run"] is True
    assert manifest["discovered"] == 3
    assert manifest["http"]["requests"] == 1, "only the listing page should be fetched"
    assert not settings.run_dir("beforward", "dry").exists()


async def test_rerun_resumes_and_does_not_duplicate(local_source, settings):
    """Re-running into the same run dir must skip work already done."""
    first = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="resume"
    )
    assert first["vehicles_written"] == 3

    second = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="resume"
    )
    assert second["vehicles_written"] == 0, "already-seen ids must be skipped"
    assert second["vehicles_total_in_file"] == 3, "existing records must be preserved"


async def test_failed_detail_is_logged_not_fatal(local_source, settings, monkeypatch):
    """One bad page must not take down the run."""
    original = local_source.parse_detail

    def flaky(html: str, url: str):
        if url.endswith("8811002"):
            raise ValueError("simulated parse explosion")
        return original(html, url)

    monkeypatch.setattr(local_source, "parse_detail", flaky)

    manifest = await run_scrape(
        local_source, [Target(make="Toyota", model="Corolla Axio")], settings, run_id="flaky"
    )
    assert manifest["vehicles_written"] == 2
    assert manifest["errors"] == 1

    errors = list(iter_ndjson(settings.run_dir("beforward", "flaky") / "errors.ndjson"))
    assert len(errors) == 1
    assert "simulated parse explosion" in errors[0]["reason"]
    assert errors[0]["url"].endswith("8811002")


async def test_multiple_targets_deduplicate_across_targets(local_source, settings):
    """The same car reachable from two targets is written once."""
    manifest = await run_scrape(
        local_source,
        [Target(make="Toyota", model="Corolla Axio"), Target(make="Toyota", model="Corolla")],
        settings,
        run_id="multi",
    )
    assert manifest["discovered"] == 3, "cross-target duplicates must collapse"
    assert manifest["vehicles_written"] == 3
