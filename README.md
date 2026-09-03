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

## Selectors are verified against real HTML

`src/scrapper/sources/beforward/fixtures/` holds two real captured pages
(`listing_corolla.html`, `detail_corolla.html`) and `tests/test_beforward_real_fixtures.py`
asserts the exact values parsed out of them. When BeForward redesigns, those tests fail and
name the field that broke.

What the real pages corrected (all of it had been guessed wrong without site access):

| | Assumed | Actual |
|---|---|---|
| Search URL | `?make=toyota&model=corolla-axio` | `/stocklist/keyword=Toyota%20Corolla%20Axio/kmode=and/page=2/sortkey=n` |
| Detail URL | `/stocklist/.../detail/123456` | `/toyota/corolla-axio/ce566767/id/16468163/` |
| Stock id | numeric | reference code `CE566767` |
| Pagination | `?page=N` | `/page=N/` path segment |

To re-capture after a site change:

```bash
uv run scrapper capture "<listing url>" -n listing_corolla
uv run scrapper capture "<detail url>" -n detail_corolla
uv run scrapper inspect src/scrapper/sources/beforward/fixtures/detail_corolla.html
```

**Everything fragile lives in one file:** `src/scrapper/sources/beforward/selectors.py`.

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

## Output format

`vehicles.json` is the import deliverable, in the consuming application's envelope:

```json
{
  "sourceCode": "beforward",
  "capturedAtUtc": "2026-09-03T09:00:00Z",
  "vehicles": [
    {
      "externalId": "CE566767",
      "listingUrl": "https://www.beforward.jp/toyota/corolla-axio/ce566767/id/16468163/",
      "make": "Toyota", "model": "Corolla Axio", "variant": "HYBRID G",
      "year": 2019, "mileage": 108333, "mileageUnit": "km",
      "steering": "rhd", "fuelType": "hybrid", "transmission": "automatic",
      "drivetrain": "2wd",
      "price": 6180, "currency": "USD", "priceType": "FOB",
      "chassisNumber": "NKE165-7161960",
      "portOfLoading": "NAGOYA",
      "destinationMarkets": ["PK", "KE", "TZ"],
      "imageUrls": ["https://image-cdn.beforward.jp/large/202608/16468163/CE566767_689f14.jpg"],
      "isAvailable": true,
      "lastSeenAtUtc": "2026-09-03T09:00:00Z"
    }
  ]
}
```

That is a real record from the captured page. Four things about it are worth knowing:

- **`destinationMarkets` is your config, not BeForward's data.** The site publishes nothing of
  the kind. Set it in `targets.yml` or with `--markets PK,KE`.
- **`portOfLoading` is BeForward's `Location` field** — the yard the car sits in. There is no
  port-of-loading field on the site; this is the closest honest proxy, not a confirmed port.
- **`priceType: "FOB"`** is BeForward's headline-price convention. It is not labelled on the page.
- **`drivetrain` is only ever `2wd` / `4wd`.** BeForward never distinguishes front from rear, so
  the scraper does not guess `fwd`/`rwd`.
- **`isAvailable` is `null` when unknown**, never assumed `true` — not reading an availability
  field is not evidence a car is in stock.

### The internal record (`vehicles.ndjson`)

The NDJSON keeps considerably more per vehicle: `engine_cc`, `engine_code`, `body_type`, `color`,
`doors`, `seats`, `condition`, `content_hash`, and a `raw` dict holding **every** spec row
verbatim (`Dimension`, `Weight`, `M3`, …). Two consequences:

- Needing a new field later never means re-scraping — it is already on disk.
- `content_hash` excludes `scraped_at`, so re-scraping an unchanged listing yields the same hash.
  Free change detection.

`src/scrapper/export.py` maps the internal record to the envelope; the parsers never know about
the export shape.

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
uv run pytest          # 132 tests, no network required
```

- `test_normalize.py` — value parsing ("2.0L" → 2000cc, "55,000 miles" → 88514 km)
- `test_beforward_parse.py` — parsing strategy against synthetic fixtures
- `test_beforward_real_fixtures.py` — pins selectors to real captured BeForward HTML
- `test_export.py` — the consumer envelope: field set, enums, markets injection
- `test_pipeline_e2e.py` — the whole pipeline against a local HTTP server: pagination,
  dedupe, resume, error isolation, output correctness

## Scope

Collects public listing data only. `robots.txt` is honoured, requests are rate limited and
cached to minimise load, and nothing behind a login is touched. Review BeForward's Terms of
Service for your intended use of the data.
