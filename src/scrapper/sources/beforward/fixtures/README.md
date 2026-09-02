# Real captured fixtures

This directory holds **real HTML captured from beforward.jp**, used to develop and
regression-test the parsers offline.

It is empty until someone runs the capture command from a machine that can reach
the site (the cloud dev environment's egress policy blocks beforward.jp):

```bash
scrapper capture "https://www.beforward.jp/stocklist/?make=toyota&model=corolla-axio" -n listing_corolla
scrapper capture "<any car detail page URL from that listing>" -n detail_corolla
```

Then commit the resulting `.html` files. `tests/test_beforward_real_fixtures.py`
picks them up automatically and starts asserting against them; until then those
tests skip and the synthetic fixtures in `tests/fixtures/synthetic/` carry the
regression coverage.
