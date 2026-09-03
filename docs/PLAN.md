# BeForward Car Scraper — Project Plan

> **Status: BUILT AND TESTED.** 28 files, ~3,000 lines, 98 tests passing, 4 skipped.
> One open item remains, tracked at the bottom: verifying the BeForward selectors
> against real HTML captured from the live site.

## Context

You need detailed car records as JSON files to import into another application. The repo
(`mushikhan2212-create/Scrapper`) was empty — no code, no commits. This is a greenfield build.

Starting scope is **beforward.jp only**, narrowed to a few makes/models. The stated end state
is aggregating vehicles from **multiple car providers**, so the MVP must not bake BeForward
assumptions into the core: the schema, storage layer and pipeline stay source-agnostic from
day one, and BeForward is simply the first plug-in source. That costs almost nothing now and
saves a rewrite in phase 2.

**Decisions locked in (from your answers):**
- Stack: Python + `httpx` + `selectolax` (no framework)
- Output: NDJSON (stream-friendly) + a `vehicles.json` array per run
- MVP targets: Toyota Corolla family, plus Nissan X-Trail and Honda Fit (my pick — high
  inventory, different body types, so they exercise varied spec layouts and deep pagination)

### ⚠️ Network constraint

`www.beforward.jp` is **blocked by this cloud session's egress proxy** (`403 CONNECT`). Only
package registries are reachable. The domain was never unblocked during the build, so the
scraper was developed in **fixture mode** — see "Open item 2" below for how to close this.

---

## Architecture

```
pyproject.toml                     # uv-managed; httpx, selectolax, pydantic v2, tenacity, typer, PyYAML, pytest
targets.yml                        # which make/model to scrape + per-target limits
src/scrapper/
  cli.py                           # typer CLI: scrape | capture | inspect | validate | stats | sources
  config.py                        # concurrency, delays, UA, output dir, cache dir
  models.py                        # pydantic Vehicle — the source-agnostic schema
  normalize.py                     # "1,234 km" -> 1234, "1.5L" -> 1500, fuel/trans enums, dates
  http.py                          # AsyncClient: retries, backoff, rate-limit, robots, disk cache
  storage.py                       # streaming NDJSON + JSON writers, run manifest, dedupe/resume
  pipeline.py                      # orchestration, source-agnostic
  sources/
    base.py                        # Source protocol every provider implements
    registry.py                    # name -> Source class
    beforward/
      source.py                    # BeForwardSource: URL building, listing + detail parsing
      selectors.py                 # ALL CSS selectors — the only fragile file
      fixtures/                    # real captured HTML (empty until captured)
tests/
  test_normalize.py                # pure unit tests, no network
  test_beforward_parse.py          # parsing strategy vs synthetic fixtures
  test_beforward_real_fixtures.py  # pins selectors to the live site (skips until captured)
  test_pipeline_e2e.py             # whole pipeline vs a local HTTP server
data/                              # run output (gitignored)
```

### The source contract (`sources/base.py`)

This is what makes phase 2 cheap. Every provider implements the same methods:

```python
class Source(Protocol):
    name: str
    def listing_url(self, target: Target, page: int) -> str: ...
    def parse_listing(self, html: str, url: str) -> ListingPage: ...
    def parse_detail(self, html: str, url: str) -> Vehicle: ...
```

Adding a second provider = one new directory + one registry line. The pipeline, storage,
schema, retry logic and CLI are untouched.

---

## The schema (`models.py`) — most important design decision

A pydantic `Vehicle` with a **typed core + verbatim raw** two-layer design:

| Group | Fields |
|---|---|
| Identity | `source`, `source_id`, `source_url`, `scraped_at`, `content_hash` |
| Vehicle | `make`, `model`, `grade`, `model_code`, `year`, `month`, `body_type`, `doors`, `seats` |
| Mechanical | `mileage_km`, `engine_cc`, `engine_code`, `fuel`, `transmission`, `drivetrain`, `steering` |
| Commercial | `price` (currency/fob/cif/total), `availability`, `location`, `port` |
| Descriptive | `color`, `condition`, `chassis_no`, `features[]`, `images[]` |
| Escape hatch | `raw: dict[str, str]` — **every** spec row captured verbatim |

Three decisions worth knowing:

- **`raw` keeps everything.** A spec we do not model yet still reaches your JSON, so needing a
  new field later never means re-scraping.
- **`content_hash` excludes `scraped_at`.** Re-scraping an unchanged listing yields the same
  hash — free change detection.
- **No currency conversion.** Amounts stay in the listing's own currency; a stale FX rate baked
  into stored records causes more problems than it solves.

Only `source`, `source_id`, `source_url` are required — a missing spec never loses a record.

---

## Pipeline

1. Read `targets.yml` → targets (make, model, filters, `max_pages`).
2. Check `robots.txt` once per host; honour any `Crawl-delay`.
3. Per target: fetch listing pages, discovering total page count.
4. Parse listing cards → detail URL + stock id + card-level fallback fields.
5. Dedupe on `(source, source_id)` against `seen.txt` — makes runs **resumable**.
6. Fetch detail pages with bounded concurrency and jittered delay.
7. Parse: **prefer JSON-LD**, then label-text spec mapping, then CSS. Validate via pydantic.
8. Append to `vehicles.ndjson` **as we go**; at the end stream out `vehicles.json` + `manifest.json`.
9. Failures → `errors.ndjson`. **A run never aborts on one bad record.**

Output layout:
```
data/beforward/2026-09-02T13-40-00Z/
  vehicles.ndjson   vehicles.json   errors.ndjson   manifest.json   seen.txt
```

### Resilience & politeness

`tenacity` backoff on 429/5xx honouring `Retry-After`; global concurrency cap (4) with jitter
(0.75s); realistic UA and persistent cookie session; on-disk HTTP cache (`--cache`) so selector
development costs zero requests; `--limit` / `--max-pages` / `--dry-run` to keep test runs small.

### Selector fragility mitigation

- All selectors in `selectors.py`, each a chain of candidates.
- **JSON-LD preferred** over CSS — a published contract, not presentation.
- **Spec tables read as label/value pairs mapped by label text** ("Mileage"), never by column
  position or class name, so a restyle cannot silently mis-assign fields.
- Fixture-based tests catch site changes; `scrapper stats` shows field coverage collapsing.

---

## CLI

```bash
scrapper scrape --target toyota:corolla-axio --max-pages 1 --limit 5
scrapper scrape --all [--dry-run] [--cache]
scrapper capture <url> -n <name>          # save real HTML as a fixture
scrapper inspect <fixture.html> [--listing]
scrapper validate data/beforward/<run>/vehicles.ndjson
scrapper stats    data/beforward/<run>/vehicles.ndjson
```

---

## Build status

| # | Step | State |
|---|---|---|
| 1 | Recon against a real page | ⚠️ **blocked** — beforward.jp unreachable (see open item 2) |
| 2 | Skeleton, config, `models.py`, `normalize.py` + tests | ✅ done |
| 3 | `parse_listing` | ✅ done |
| 4 | `parse_detail` | ✅ done |
| 5 | Pipeline + storage + CLI, end-to-end | ✅ done |
| 6 | Cache, resume, retries, manifest, README | ✅ done |

**98 tests pass, 4 skip** (the real-fixture tests, awaiting captured HTML).
Verified end-to-end against a local HTTP server: pagination, cross-target dedupe, resume,
error isolation, and output correctness.

---

## Phase 2 (multi-source)

1. Create `src/scrapper/sources/<provider>/` implementing the `Source` protocol.
2. Register it in `sources/registry.py`.
3. Add its targets to `targets.yml`.

Then an `aggregate` command merges runs across sources, deduping on a fuzzy key
(make + model + year + mileage + chassis fragment). No core changes required.

---

## Open item

### Verify selectors against real BeForward HTML

The parsers are defensive but **unverified against a live page**. From a machine that can
reach the site:

```bash
uv run scrapper capture "https://www.beforward.jp/stocklist/?make=toyota&model=corolla-axio" -n listing_corolla
uv run scrapper capture "<a detail URL from that listing>" -n detail_corolla
```

Commit the two HTML files and re-run `pytest`. The 4 skipped tests activate and report exactly
which selectors need adjusting, naming the spec labels actually found. Everything fixable lives
in `src/scrapper/sources/beforward/selectors.py`.

---

## Verification

```bash
uv run pytest                              # 98 passed, 4 skipped, no network needed
uv run scrapper inspect tests/fixtures/synthetic/detail_jsonld.html   # sample record
git ls-remote origin                       # after push: branch ref present
```

## Constraints

Public listing data only. `robots.txt` honoured, requests rate limited and cached to minimise
load, nothing behind a login touched. BeForward's Terms of Service are yours to review for your
intended use of the data.
