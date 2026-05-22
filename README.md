# diets

A self-contained, single-file HTML browser for grocery-item nutrition. Open
`diets.html` in any browser — no server, no install. Filter by multiple
nutrients at once (zero cholesterol + zero sugar + low sodium, etc.), with a
live insight panel that shows the distribution of the current selection
alongside FDA Daily Value references.

## Data sources

Walmart's own site blocks scrapers (ToS + bot protection), so we pull from
open datasets instead:

- **Open Food Facts** (`data/openfood.csv`) — community-contributed grocery
  nutrition labels, per 100 g
- **OpenLabel / Wikifood** (`data/products-3000.csv`) — branded nutrition
  panels, normalized to per 100 g

Most national-brand items in these datasets are sold at Walmart. The result
isn't strictly the Walmart catalog, but it covers the same shopping universe.

## Rebuild

```
python3 build.py
```

That regenerates `diets.html` from the CSVs in `data/`. Add more rows to
either CSV and rebuild to grow the catalog.
