# ham-analysis

## ➡️ [**View the live maps and tables → swilcox.github.io/ham-analysis**](https://swilcox.github.io/ham-analysis/) ⬅️

[![Live site](https://img.shields.io/badge/Live%20site-swilcox.github.io%2Fham--analysis-blue?style=for-the-badge)](https://swilcox.github.io/ham-analysis/)
[![Monthly site build](https://github.com/swilcox/ham-analysis/actions/workflows/monthly-pages.yml/badge.svg)](https://github.com/swilcox/ham-analysis/actions/workflows/monthly-pages.yml)

> **The site is rebuilt automatically on the 1st of every month** (12:00 UTC) from the latest FCC ULS and US Census data — no need to run anything locally to see current results. The build date for the data you're looking at is shown on the site itself.

---

Geographic analysis of **US amateur radio licenses** using public **FCC ULS** data and **US Census** population / boundaries.

Maps and tables answer questions like:

- Where are most hams (absolute counts)?
- Where is density highest (licenses per 100,000 residents)?
- Where have **new grants** grown over the past *X* months (absolute and per 100k)?
- How does operator class mix (Technician / General / Extra) vary by place?
- Do **older counties** have higher ham density? (ecological correlation — see Age analysis)

## Important caveats

1. **Mailing address ≠ station location.** FCC geography is the address on the license (PO boxes, clubs, and stale addresses bias local counts).
2. **Growth** is defined as *new grant dates* in a rolling window on currently active licenses — not net change from expirations/cancellations.
3. **ZIP → county** uses the Census ZCTA–county relationship file (primary county = largest land-area overlap). Multi-county ZIPs are approximated.
4. **FCC does not publish licensee ages.** Age analysis correlates *county* median age / %65+ with *county* licenses per 100k (ecological correlation), not the ages of individual hams.

## Quick start

Requires Python 3.11+ and network access for the first run (FCC dump is ~150–200 MB).

```bash
# From the repo root
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Full pipeline (download → load → geo → metrics → maps → age → site)
ham all

# Or with a custom growth window
ham all --months 6

# Landing page only (after maps exist)
ham site
open outputs/site/index.html
```

Without `uv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ham all
```

### Outputs

| Path | Description |
|------|-------------|
| `outputs/tables/metrics_state.csv` | State counts, per 100k, new grants, class mix |
| `outputs/tables/metrics_county.csv` | County metrics (all counties; zeros where none) |
| `outputs/maps/*.html` | Interactive Plotly choropleths (MapLibre; open in a browser) |
| `outputs/site/index.html` | Landing page + copied maps/tables for GitHub Pages |
| `data/processed/*.parquet` | Intermediate tables (licenses, geo join, metrics) |

## GitHub Pages (monthly refresh)

**Published site: <https://swilcox.github.io/ham-analysis/>**

A [monthly cron](.github/workflows/monthly-pages.yml) (1st of each month, 12:00 UTC) re-downloads FCC/Census data, rebuilds the maps, and deploys `outputs/site/` to Pages. It can also be run on demand from **Actions → “Monthly site build” → Run workflow**.

To set this up on your own fork:

1. **Create the GitHub repo** and push this project (public is easiest for free Pages + Actions).
2. **Enable Pages from Actions:** repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Run once manually** via `workflow_dispatch`, then let the cron take over. Your site lands at `https://<user>.github.io/<repo>/`.

Local equivalent of what CI runs:

```bash
ham all --months 12 --force
# publishes under outputs/site/ (index.html, maps/, tables/, meta.json)
```

Choropleths use **simplified** Census boundaries and Plotly’s MapLibre renderer so pan/zoom stays responsive. County pages were ~23 MB with full-detail SVG geo; they should be much smaller after `ham map`.

**Color scales** (continuous maps) are clipped to the 2nd–98th percentile of each metric so a handful of extreme counties (often very small populations with high per-capita rates) do not flatten the rest of the map. Hover still shows the true value; outliers share the top/bottom color.

**Quantile maps** (filenames ending in `_quantile.html`) use equal-count bins (quintiles): each color class has ~the same number of counties, so rank differences in the middle of the distribution stay visible. Examples:

- `county_licenses_per_100k_quantile.html`
- `county_new_grants_per_100k_quantile.html`
- `county_pct_65plus_quantile.html`

### Incremental commands

```bash
ham download          # FCC l_amat.zip + Census ACS/boundaries/crosswalk
ham download-fcc
ham download-census
ham load              # Parse HD/EN/AM → licenses.parquet
ham geo               # ZIP → county + population
ham aggregate -m 12   # Metrics
ham map -m 12         # HTML maps
ham age               # Density vs median age / %65+ correlation + scatter plots
```

Use `--force` to re-download or rebuild cached artifacts.

### Age analysis

FCC ULS public files have **no date of birth**, so we cannot compute the average age of license holders. Instead:

1. Download county **median age** and **% age 65+** from Census population estimates.
2. Join those to county license metrics.
3. Report Pearson/Spearman correlations and interactive scatter plots.

```bash
ham download-census   # includes age/sex estimates
ham aggregate --force
ham age
open outputs/maps/county_density_vs_median_age.html
open outputs/tables/age_correlation.csv
```

## Data sources

| Source | What we use |
|--------|-------------|
| [FCC ULS complete amateur licenses](https://data.fcc.gov/download/pub/uls/complete/l_amat.zip) | `HD.dat` (status, dates), `EN.dat` (address), `AM.dat` (operator class) |
| [Census Population Estimates](https://www2.census.gov/programs-surveys/popest/datasets/) (`co-est2024-alldata.csv`) | Total population by state and county (no API key) |
| Census county age/sex estimates (`cc-est2024-agesex-all.csv`) | Median age and population 65+ by county |
| Census Cartographic Boundary Files (500k) | State/county polygons for maps |
| Census 2020 ZCTA–county relationship | ZIP/ZCTA → county FIPS |

Active licenses only (`license_status = 'A'`). One row per call sign (highest system id among actives). Licensee entity rows only (`entity_type = 'L'`).

## Development

```bash
pytest
```

Tests use tiny fixture `.dat` files and do **not** download the full FCC dump.

## Project layout

```text
src/ham_analysis/     # download, load, geo, aggregate, maps, CLI
data/raw/             # downloads (gitignored)
data/processed/       # parquet + duckdb (gitignored)
outputs/tables|maps/  # deliverables
tests/                # fixture-based unit tests
```

## Follow-ups (not in v1)

- True **net growth** from weekly snapshots or daily ULS transaction files
- ZCTA / metro density maps
- **RBN** (Reverse Beacon Network) activity joined to call signs
- Streamlit or a small web UI

## License

Analysis code is available for personal/research use. Underlying FCC and Census data remain public domain / public data from those agencies.
