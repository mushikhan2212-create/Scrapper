# BeForward Scraper — selector fixes + export format

> **Status: DONE.** All items below are implemented and verified against the captured
> pages. 132 tests pass. The one remaining unknown is live fetching (pagination beyond
> page 1, rate limits), which only a machine that can reach beforward.jp can prove.

## Context

You supplied two real BeForward pages as zips, and specified the exact JSON shape your other
application imports. I unpacked both and ran the existing parser against them. The good news:
the detail parser mostly works. The bad news: **the listing parser found 0 vehicles**, because
every URL assumption I made without site access was wrong.

This plan fixes what the real HTML disproves, then adds an export layer emitting your format.

### What the real pages showed

| Thing | I assumed | Reality |
|---|---|---|
| Search URL | `?make=toyota&model=corolla-axio` | `/stocklist/keyword=Toyota%20Corolla%20Axio/kmode=and/page=2/sortkey=n` — **path segments, not query strings** |
| Detail URL | `/stocklist/.../detail/123456` | `/toyota/corolla-axio/ce566767/id/16468163/` |
| Stock id | numeric | **reference code** `CE566767` (2 letters + 6 digits) |
| Pagination | `?page=N` | `/page=N/` path segment |

Result: **0 of 25 detail links matched**, so a real run would have scraped nothing.

What already works: JSON-LD is present (`Product`/`Car` with `sku`, `price`, `availability`,
image CDN URLs), the spec table `table.specification` parses correctly through the existing
label-text mapping, and 19 of 20 spec rows were captured verbatim.

### Decisions you made

- Emit your envelope as `vehicles.json` (the deliverable); keep the rich `vehicles.ndjson` alongside
- `destinationMarkets` comes from per-run config — it does not exist on BeForward's pages
- Currency emitted as the site states it (**USD 6,180** on this car, not JPY)
- `drivetrain` is `"2wd"` / `"4wd"` only — BeForward never distinguishes front from rear

---

## 1. Fix `sources/beforward/selectors.py`

- **`DETAIL_URL_PATTERNS`** — add the real form, capturing the reference code:
  `r"/([a-z]{2}\d{5,})/id/\d+/?"` (case-insensitive). Keep existing patterns as fallbacks.
- **Pagination** — match `page=N` as a path segment (`[/?&]page=(\d+)`), not just a query param.
- **`LABEL_MAP`** — add the labels the real page actually uses:
  `extcolor` → `color`, `registrationyearmonth` / `manufactureyearmonth` → `year_month`,
  `refno` already maps, `versionclass` → `grade` already maps, `drive` → `drivetrain` already maps.
- **`FEATURE_SELECTORS`** — currently matches site chrome, producing junk
  (`["Favorites", "Notify Me", "Save Search", "Easy Inquiry"]`). Narrow to the equipment block,
  and drop anything matching a UI-noise denylist.

## 2. Fix `sources/beforward/source.py`

- **`listing_url()`** — build keyword search URLs:
  `{base}/stocklist/keyword={quoted make + model}/kmode=and/page={n}/sortkey=n`.
  This avoids needing BeForward's internal numeric make/model ids (`make=1/model=132`), which
  would otherwise require scraping and maintaining an id map.
- **`_total_pages()`** — scan *every* anchor href for `page=(\d+)` and take the max, rather than
  relying on a pagination CSS container. On the real page this yields **11**; the CSS approach
  yielded `None`.
- **Breadcrumb extraction (new)** — the `BreadcrumbList` JSON-LD carries `model`, `body_type` and
  `year`, none of which appear in the `Product` block or the spec table. This fixes `model: null`
  and `body_type: null`. Reuse the existing `_walk_jsonld` helper.
- **`condition`** — from `offers.itemCondition` (`UsedCondition` → `used`).
- **`make`** — title-case `"TOYOTA"` → `"Toyota"`.

## 3. Fix `normalize.py`

`parse_drivetrain` returns `other` for the real value `"2wheel drive"`. Add
`2wheel`/`4wheel` handling so it returns `2wd` / `4wd`. Everything else parsed correctly
(`108,333 km` → `108333`, `1,490cc` → `1490`, `Hybrid(Petrol)` → `hybrid`).

## 4. New: `src/scrapper/export.py`

A pure mapper from the internal `Vehicle` to your envelope. Kept separate from `models.py` so the
scraped record stays lossless and the export shape can change without touching parsing.

```python
def to_export_record(v: Vehicle, markets: list[str]) -> dict: ...
def build_envelope(vehicles: Iterable[Vehicle], source: str, markets: list[str]) -> dict: ...
```

Field mapping, verified against the real car:

| Your field | Source | Real value |
|---|---|---|
| `externalId` | spec `Ref. No.` / JSON-LD `sku` | `"CE566767"` |
| `listingUrl` | canonical URL | `.../ce566767/id/16468163/` |
| `make` / `model` | brand + breadcrumb | `"Toyota"` / `"Corolla Axio"` |
| `variant` | `Version/Class` | `"HYBRID G"` |
| `year` | `RegistrationYear/month` | `2019` |
| `mileage` / `mileageUnit` | `Mileage` | `108333` / `"km"` |
| `steering` | `Steering` | `"rhd"` |
| `fuelType` / `transmission` | spec | `"hybrid"` / `"automatic"` |
| `drivetrain` | `Drive` | `"2wd"` |
| `price` / `currency` / `priceType` | JSON-LD offer | `6180` / `"USD"` / `"FOB"` |
| `chassisNumber` | `Chassis No.` | `"NKE165-7161960"` |
| `portOfLoading` | `Location` | `"NAGOYA"` |
| `destinationMarkets` | run config | `["PK","KE","TZ"]` |
| `imageUrls` | JSON-LD `image` | 3 CDN URLs |
| `isAvailable` | `availability` | `true` |
| `lastSeenAtUtc` | per-vehicle scrape time | ISO-8601 Z |

Two judgement calls, flagged rather than hidden:

- **`externalId` = `"CE566767"`**, the site's own reference — not your example's `"BF-"` prefix,
  since `sourceCode` already namespaces it. One-line change if you want the prefix.
- **`portOfLoading` = `Location`.** BeForward publishes no port-of-loading field; `Location`
  (`NAGOYA`) is the vehicle's yard and the closest honest proxy. It is a location, not a
  confirmed loading port.
- **`priceType: "FOB"`** is BeForward's headline-price convention, not a label on the page.

## 5. Wire it in

- `storage.py` — `_write_json_array` becomes `_write_export_envelope`, emitting
  `{sourceCode, capturedAtUtc, vehicles:[...]}`. `vehicles.ndjson` keeps the full internal record.
- `targets.yml` — add `destination_markets: ["PK","KE","TZ"]` per source.
- `cli.py` — `--markets` override.

## 6. Fixtures and tests

- Extract `detail_corolla.html` (604 KB) and `listing_corolla.html` (1 MB) from the zips into
  `src/scrapper/sources/beforward/fixtures/`, and **delete the two `.zip` files** — they carry
  ~4 MB of JS, images and tracking assets that the tests never read.
- `test_beforward_real_fixtures.py` already targets `listing*.html` / `detail*.html` and will
  activate automatically. Tighten it to assert the real values above.
- New `test_export.py` — envelope shape, enum values, `destinationMarkets` injection.

---

## Verification

```bash
uv run pytest                                   # existing 98 + real-fixture + export tests
uv run scrapper inspect src/scrapper/sources/beforward/fixtures/listing_corolla.html --listing
#   -> expect 25 items, total_pages=11 (currently: 0 items, None)
uv run scrapper inspect src/scrapper/sources/beforward/fixtures/detail_corolla.html
#   -> expect model="Corolla Axio", body_type="Sedan", color="Pearl",
#      drivetrain="2wd", month=3, and no junk in features
```

Then a live smoke run once the site is reachable from your machine:

```bash
uv run scrapper scrape --target toyota:"corolla axio" --max-pages 1 --limit 5
jq '.vehicles[0]' data/beforward/*/vehicles.json     # your exact envelope
```

I will paste the generated record for the real Corolla Axio so you can confirm the shape against
your importer **before** running it at volume.

## Still unverifiable here

beforward.jp remains blocked from this container, so pagination beyond page 1, the resume path
against live data, and whether the site rate-limits are all untested against the live host. The
fixtures prove parsing; only your machine can prove fetching.
