# Scrapper

Scrapes used-car listings into JSON for import into another application.

BeForward (beforward.jp) is the first source. The schema, storage and pipeline are
source-agnostic, so adding further providers later is one new directory — not a rewrite.

---

## Quick start

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"

# Verify everything works offline first:
uv run pytest

# Then a small real run:
uv run scrapper scrape --target toyota:corolla-axio --max-pages 1 --limit 5
```

Output lands in `data/beforward/<timestamp>/`:

| File | What it is |
|---|---|
| `vehicles.ndjson` | One vehicle per line. Written as the run proceeds — **stream-import this** |
| `vehicles.json` | The same records as a single JSON array |
| `errors.ndjson` | Any URL that failed, with the reason, so it can be retried |
| `manifest.json` | Run summary: counts, timings, targets, HTTP stats |
| `seen.txt` | Stock ids already written — makes a re-run resume rather than duplicate |

---

## ⚠️ First-run step: capture real fixtures

The parsers are written defensively (JSON-LD first, then label-text matching, then CSS),
but **the selectors have not yet been checked against a real BeForward page** — the
development environment's network policy blocks beforward.jp.

From a machine that can reach the site, run this once:

```bash
uv run scrapper capture "https://www.beforward.jp/stocklist/?make=toyota&model=corolla-axio" -n listing_corolla
uv run scrapper capture "<a car detail URL from that listing>" -n detail_corolla
```

That saves the HTML into `src/scrapper/sources/beforward/fixtures/`. Commit those files and
run `pytest` again: `tests/test_beforward_real_fixtures.py` stops skipping and tells you
precisely which selectors — if any — need adjusting, naming the spec labels it actually found.

To iterate on a fixture without touching the network:

```bash
uv run scrapper inspect src/scrapper/sources/beforward/fixtures/detail_corolla.html
uv run scrapper inspect src/scrapper/sources/beforward/fixtures/listing_corolla.html --listing
```

**Everything that needs changing lives in one file:** `src/scrapper/sources/beforward/selectors.py`.

---

## Commands

```bash
scrapper scrape --target toyota:corolla-axio    # one target
scrapper scrape --all                           # every enabled target in targets.yml
scrapper scrape --all --dry-run                 # discover detail URLs, fetch nothing else
scrapper scrape -t nissan:x-trail --cache       # reuse cached HTML while developing

scrapper inspect <fixture.html> [--listing]     # parse a saved page, print the result
scrapper validate data/beforward/<run>/vehicles.ndjson
scrapper stats    data/beforward/<run>/vehicles.ndjson    # field coverage report
scrapper sources                                # list providers
```

Useful flags: `--max-pages`, `--limit`, `--concurrency`, `--delay`, `--verbose`.

`scrapper stats` is the fastest way to spot a broken selector after a site change: a field
that drops from 100% to 0% coverage points straight at the problem.

---

## What to scrape

`targets.yml` — adding a make/model is a new list entry, no code change:

```yaml
sources:
  beforward:
    - make: Toyota
      model: Corolla Axio
      max_pages: 2
      enabled: true
```

Start with `max_pages: 1` while verifying selectors, then raise it.

---

## The record schema

Every vehicle has a **typed core** plus a `raw` dict holding every spec row verbatim.

```json
{
  "source": "beforward",
  "source_id": "8811001",
  "source_url": "https://www.beforward.jp/...",
  "scraped_at": "2026-09-02T13:35:15Z",
  "content_hash": "7b37e7997bfc8869",
  "make": "Toyota", "model": "Corolla Axio", "grade": null,
  "model_code": "DBA-NRE160", "year": 2015, "month": 3,
  "body_type": "Sedan", "doors": 4, "seats": 5,
  "mileage_km": 78000, "engine_cc": 1500,
  "fuel": "petrol", "transmission": "cvt", "drivetrain": "2wd", "steering": "right",
  "price": { "currency": "USD", "fob_amount": 4850.0, "raw": "USD 4850" },
  "availability": "InStock", "location": "Japan",
  "color": "Pearl White", "chassis_no": "NRE160-71xxxxx",
  "features": ["Air Conditioner", "Power Steering"],
  "images": ["https://img.beforward.jp/cars/8811001/1.jpg"],
  "raw": { "Dimension (L*W*H)": "4360*1695*1460", "Weight": "1090 kg" }
}
```

Three decisions worth knowing about:

- **`raw` keeps everything.** A spec we do not model yet still reaches your JSON, so needing a
  new field later never means re-scraping.
- **`content_hash` excludes `scraped_at`.** Re-scraping an unchanged listing produces the same
  hash, which gives you change detection for free.
- **No currency conversion.** Amounts stay in the listing's own currency; a stale FX rate baked
  into stored records causes more problems than it solves.

Only `source`, `source_id` and `source_url` are required — a missing spec never loses a record.

---

## Adding another provider (phase 2)

1. Create `src/scrapper/sources/<provider>/` with a class implementing the `Source` protocol
   in `sources/base.py`: `listing_url`, `parse_listing`, `parse_detail`.
2. Register it in `sources/registry.py`.
3. Add its targets to `targets.yml`.

The pipeline, schema, storage, HTTP layer and CLI need no changes. Reuse `normalize.py` for
value parsing — it is provider-neutral.

---

## Design notes

**Resilient parsing.** JSON-LD is preferred over CSS because it is a published contract rather
than presentation. The spec table is read as label/value *pairs* mapped by what the label says
("Mileage"), never by column position or class name — so a restyle cannot silently mis-assign
fields. CSS selectors are the last resort, and each is a chain of candidates.

**Politeness.** Conservative defaults: 4 concurrent requests, 0.75s + jitter between them,
`robots.txt` checked and honoured (including `Crawl-delay`), exponential backoff on 429/5xx
honouring `Retry-After`. Tune in `config.py` or via `--concurrency` / `--delay`.

**Failure isolation.** One bad page is logged to `errors.ndjson` and the run continues. Records
are flushed to NDJSON as they are parsed, so an interrupted run still leaves usable data and
`seen.txt` lets the next run pick up where it stopped.

**Development cache.** `--cache` stores responses on disk, so iterating on selectors costs zero
requests. Off by default so real runs get fresh data.

## Testing

```bash
uv run pytest          # 98 tests, no network required
```

- `test_normalize.py` — value parsing ("2.0L" → 2000cc, "55,000 miles" → 88514 km)
- `test_beforward_parse.py` — parsing strategy against synthetic fixtures
- `test_beforward_real_fixtures.py` — pins selectors to the live site (skips until captured)
- `test_pipeline_e2e.py` — the whole pipeline against a local HTTP server: pagination,
  dedupe, resume, error isolation, output correctness

## Scope

Collects public listing data only. `robots.txt` is honoured, requests are rate limited and
cached to minimise load, and nothing behind a login is touched. Review BeForward's Terms of
Service for your intended use of the data.
