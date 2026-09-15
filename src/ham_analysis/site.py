"""Build a static GitHub Pages site: landing page + maps + tables."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from ham_analysis.config import (
    CENSUS_CB_YEAR,
    CENSUS_RAW_DIR,
    DEFAULT_GROWTH_MONTHS,
    FCC_AMAT_LICENSE_URL,
    FCC_AMAT_ZIP_NAME,
    FCC_AMAT_APPLICATION_URL,
    FCC_AMAT_APPLICATION_ZIP_NAME,
    FCC_RAW_DIR,
    OUTPUT_MAPS,
    OUTPUT_TABLES,
    ROOT,
    ensure_dirs,
)

SITE_DIR = ROOT / "outputs" / "site"
SITE_MAPS = SITE_DIR / "maps"
SITE_TABLES = SITE_DIR / "tables"
SITE_META = SITE_DIR / "meta.json"


@dataclass(frozen=True)
class MapPage:
    filename: str
    title: str
    blurb: str
    section: str


# Catalog of published maps (order within each section is display order).
MAP_CATALOG: list[MapPage] = [
    # State — continuous
    MapPage(
        "state_license_count.html",
        "Active licenses by state",
        "Absolute count of active amateur licenses with a mailing address in each state.",
        "State maps",
    ),
    MapPage(
        "state_licenses_per_100k.html",
        "Licenses per 100,000 residents (state)",
        "Density of active licenses relative to state population. Better for comparing "
        "sparsely vs densely populated states than raw counts.",
        "State maps",
    ),
    MapPage(
        "state_new_grants.html",
        "New grants — last 12 months (state)",
        "Active licenses whose FCC grant date falls in the last 12 months. A proxy for "
        "new licensees, not net stock change from expirations.",
        "State maps",
    ),
    MapPage(
        "state_new_grants_per_100k.html",
        "New grants per 100,000 (state)",
        "Same 12-month grant window, scaled by population so large and small states "
        "are comparable.",
        "State maps",
    ),
    # County — continuous
    MapPage(
        "county_license_count.html",
        "Active licenses by county",
        "Absolute active-license counts by county. Large metro areas dominate.",
        "County maps (continuous scale)",
    ),
    MapPage(
        "county_licenses_per_100k.html",
        "Licenses per 100,000 residents (county)",
        "County density with a continuous color scale clipped to the bulk of the "
        "distribution (2nd–98th percentile) so a few extreme small-population counties "
        "do not wash out mid-range differences. Hover shows the true rate.",
        "County maps (continuous scale)",
    ),
    MapPage(
        "county_new_grants.html",
        "New grants — last 12 months (county)",
        "Count of licenses with grant dates in the last 12 months, by county.",
        "County maps (continuous scale)",
    ),
    MapPage(
        "county_new_grants_per_100k.html",
        "New grants per 100,000 (county)",
        "Population-scaled growth proxy, continuous color scale (clipped percentiles).",
        "County maps (continuous scale)",
    ),
    MapPage(
        "county_median_age.html",
        "County median age",
        "Census population-estimates median age of each county’s whole population "
        "(not the ages of license holders — FCC does not publish DOB).",
        "County maps (continuous scale)",
    ),
    MapPage(
        "county_pct_65plus.html",
        "Share of population age 65+",
        "Percent of county residents age 65 and older (Census estimates).",
        "County maps (continuous scale)",
    ),
    # County — quantile
    MapPage(
        "county_licenses_per_100k_quantile.html",
        "Licenses per 100,000 (county, quintiles)",
        "Same density metric, but colored by equal-count quintiles: each class has "
        "~20% of counties. Best for seeing rank differences in the middle of the "
        "distribution (e.g. 280 vs 360 per 100k).",
        "County maps (quantile bins)",
    ),
    MapPage(
        "county_new_grants_per_100k_quantile.html",
        "New grants per 100,000 (county, quintiles)",
        "12-month grants per 100k, quintile-binned for rank comparison.",
        "County maps (quantile bins)",
    ),
    MapPage(
        "county_pct_65plus_quantile.html",
        "Population age 65+ (county, quintiles)",
        "County % age 65+ in five equal-count classes. Useful next to ham-density "
        "quintile maps when exploring the age–hobby relationship.",
        "County maps (quantile bins)",
    ),
    # Age analysis
    MapPage(
        "county_density_vs_median_age.html",
        "Scatter: density vs median age",
        "Each point is a county. Tests whether older places have more hams per "
        "capita (ecological correlation only).",
        "Age analysis",
    ),
    MapPage(
        "county_density_vs_pct_65plus.html",
        "Scatter: density vs % age 65+",
        "Same idea using share of population 65+ instead of median age.",
        "Age analysis",
    ),
]

SECTION_ORDER = [
    "State maps",
    "County maps (continuous scale)",
    "County maps (quantile bins)",
    "Age analysis",
]


def _file_mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _file_mtime_date(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def collect_metadata(*, growth_months: int = DEFAULT_GROWTH_MONTHS) -> dict:
    """Gather pipeline freshness and source notes for the landing page."""
    fcc_zip = FCC_RAW_DIR / FCC_AMAT_ZIP_NAME
    popest = CENSUS_RAW_DIR / "co-est2024-alldata.csv"
    agesex = CENSUS_RAW_DIR / "cc-est2024-agesex-all.csv"
    licenses = ROOT / "data" / "processed" / "licenses.parquet"

    active_count = None
    if licenses.exists():
        try:
            import duckdb

            active_count = duckdb.execute(
                f"SELECT COUNT(*) FROM read_parquet('{licenses}')"
            ).fetchone()[0]
        except Exception:
            active_count = None

    classification_path = licenses.parent / "license_status.json"
    classification = {}
    if classification_path.exists():
        saved = json.loads(classification_path.read_text())
        classification = {"as_of": saved["as_of"], "counts": saved["counts"]}

    generated_at = datetime.now(tz=timezone.utc)
    return {
        "generated_at_utc": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "generated_date": generated_at.strftime("%Y-%m-%d"),
        "growth_months": growth_months,
        "active_license_count": active_count,
        "license_classification": classification,
        "sources": {
            "fcc_uls": {
                "name": "FCC Universal Licensing System — Amateur complete licenses",
                "url": FCC_AMAT_LICENSE_URL,
                "file": FCC_AMAT_ZIP_NAME,
                "downloaded_at": _file_mtime_iso(fcc_zip),
                "downloaded_date": _file_mtime_date(fcc_zip),
                "note": (
                    "Weekly public dump. Counts include unexpired A records and supported "
                    "timely pending renewals; grace-period and unresolved records are excluded. "
                    "Geography is mailing address on the license."
                ),
            },
            "fcc_applications": {
                "name": "FCC ULS — Amateur complete applications",
                "url": FCC_AMAT_APPLICATION_URL,
                "file": FCC_AMAT_APPLICATION_ZIP_NAME,
                "downloaded_at": _file_mtime_iso(FCC_RAW_DIR / FCC_AMAT_APPLICATION_ZIP_NAME),
            },
            "census_popest": {
                "name": "Census Bureau Population Estimates (county/state)",
                "url": (
                    "https://www2.census.gov/programs-surveys/popest/datasets/"
                    "2020-2024/counties/totals/co-est2024-alldata.csv"
                ),
                "vintage": "Vintage 2024 (POPESTIMATE2024)",
                "downloaded_at": _file_mtime_iso(popest),
                "downloaded_date": _file_mtime_date(popest),
            },
            "census_agesex": {
                "name": "Census Bureau county age/sex estimates",
                "url": (
                    "https://www2.census.gov/programs-surveys/popest/datasets/"
                    "2020-2024/counties/asrh/cc-est2024-agesex-all.csv"
                ),
                "vintage": "cc-est2024-agesex (latest YEAR code in file)",
                "downloaded_at": _file_mtime_iso(agesex),
                "downloaded_date": _file_mtime_date(agesex),
            },
            "census_boundaries": {
                "name": "Census Cartographic Boundary Files",
                "url": "https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
                "vintage": f"GENZ{CENSUS_CB_YEAR} 500k state & county",
            },
            "zcta_county": {
                "name": "Census 2020 ZCTA–county relationship file",
                "url": (
                    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
                    "tab20_zcta520_county20_natl.txt"
                ),
                "note": "Primary county = largest land-area overlap for each ZCTA/ZIP.",
            },
        },
    }


def _copy_tree_files(src: Path, dest: Path, pattern: str = "*") -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if not src.exists():
        return copied
    for path in sorted(src.glob(pattern)):
        if path.is_file() and path.name != ".gitkeep":
            shutil.copy2(path, dest / path.name)
            copied.append(path.name)
    return copied


def build_site(*, growth_months: int = DEFAULT_GROWTH_MONTHS) -> Path:
    """
    Assemble outputs/site/ for GitHub Pages: index.html, maps/, tables/, meta.json.
    """
    ensure_dirs()
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    # Fresh site directory maps/tables
    if SITE_MAPS.exists():
        shutil.rmtree(SITE_MAPS)
    if SITE_TABLES.exists():
        shutil.rmtree(SITE_TABLES)

    maps_copied = _copy_tree_files(OUTPUT_MAPS, SITE_MAPS, "*.html")
    tables_copied = _copy_tree_files(OUTPUT_TABLES, SITE_TABLES, "*.csv")

    meta = collect_metadata(growth_months=growth_months)
    meta["maps_published"] = maps_copied
    meta["tables_published"] = tables_copied
    SITE_META.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    index_path = SITE_DIR / "index.html"
    index_path.write_text(
        _render_index(meta=meta, maps_on_disk=set(maps_copied), tables=tables_copied),
        encoding="utf-8",
    )
    # Optional nojekyll for GH Pages
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print(f"  Site → {SITE_DIR}")
    print(f"  Maps: {len(maps_copied)} | Tables: {len(tables_copied)}")
    print(f"  → {index_path}")
    return index_path


def _render_index(
    *,
    meta: dict,
    maps_on_disk: set[str],
    tables: list[str],
) -> str:
    generated = escape(meta["generated_at_utc"])
    fcc = meta["sources"]["fcc_uls"]
    pop = meta["sources"]["census_popest"]
    age = meta["sources"]["census_agesex"]
    active = meta.get("active_license_count")
    active_s = f"{active:,}" if isinstance(active, int) else "n/a"
    growth = meta.get("growth_months", DEFAULT_GROWTH_MONTHS)

    classification = meta.get("license_classification", {})
    status_counts = classification.get("counts", {})
    classification_rows = "".join(
        f"<tr><th>{label}</th><td>{status_counts.get(key, 0):,}</td></tr>"
        for key, label in [
            ("unexpired", "Unexpired — included"),
            ("continued", "Pending timely renewal — included"),
            ("grace", "Within two years past expiration — excluded"),
            ("expired", "Two or more years past expiration — excluded"),
            ("unresolved", "Unresolved — excluded; see license_review.csv"),
        ]
    ) if classification else "<tr><td>Rebuild the pipeline to classify licenses.</td></tr>"

    # Group catalog entries that exist on disk
    by_section: dict[str, list[MapPage]] = {s: [] for s in SECTION_ORDER}
    extra: list[MapPage] = []
    catalog_names = {m.filename for m in MAP_CATALOG}
    for page in MAP_CATALOG:
        if page.filename in maps_on_disk:
            by_section.setdefault(page.section, []).append(page)
    for name in sorted(maps_on_disk - catalog_names):
        extra.append(
            MapPage(name, name, "Generated map (see filename).", "Other maps")
        )
    if extra:
        by_section["Other maps"] = extra
        if "Other maps" not in SECTION_ORDER:
            section_list = SECTION_ORDER + ["Other maps"]
        else:
            section_list = SECTION_ORDER
    else:
        section_list = SECTION_ORDER

    sections_html: list[str] = []
    for section in section_list:
        pages = by_section.get(section) or []
        if not pages:
            continue
        items = []
        for p in pages:
            items.append(
                f"""
            <li class="map-card">
              <a class="map-title" href="maps/{escape(p.filename)}">{escape(p.title)}</a>
              <p class="map-blurb">{escape(p.blurb)}</p>
              <p class="map-file"><code>{escape(p.filename)}</code></p>
            </li>"""
            )
        sections_html.append(
            f"""
        <section class="map-section">
          <h2>{escape(section)}</h2>
          <ul class="map-list">
            {"".join(items)}
          </ul>
        </section>"""
        )

    table_links = ""
    if tables:
        lis = "".join(
            f'<li><a href="tables/{escape(t)}">{escape(t)}</a></li>' for t in tables
        )
        table_links = f"<ul>{lis}</ul>"
    else:
        table_links = "<p><em>No CSV tables published in this build.</em></p>"

    fcc_when = escape(fcc.get("downloaded_date") or "unknown")
    pop_when = escape(pop.get("downloaded_date") or "unknown")
    age_when = escape(age.get("downloaded_date") or "unknown")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>US Amateur Radio License Geography</title>
  <meta name="description" content="Maps of FCC amateur radio licenses by state and county, with Census population and age context." />
  <style>
    :root {{
      --bg: #f6f4ef;
      --ink: #1a1a1a;
      --muted: #5a5a5a;
      --card: #ffffff;
      --accent: #b45309;
      --border: #e5e0d6;
      --link: #9a3412;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.55;
    }}
    header {{
      background: linear-gradient(135deg, #1c1917 0%, #44403c 60%, #78350f 100%);
      color: #fafaf9;
      padding: 2.5rem 1.25rem 2rem;
    }}
    header .wrap, main.wrap, footer.wrap {{
      max-width: 52rem;
      margin: 0 auto;
    }}
    h1 {{
      font-size: clamp(1.6rem, 4vw, 2.2rem);
      font-weight: 600;
      margin: 0 0 0.5rem;
      letter-spacing: -0.02em;
    }}
    .tagline {{ opacity: 0.9; margin: 0; font-size: 1.05rem; }}
    .meta-pill {{
      display: inline-block;
      margin-top: 1rem;
      padding: 0.35rem 0.75rem;
      border-radius: 999px;
      background: rgba(255,255,255,0.12);
      font-size: 0.9rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    main.wrap {{ padding: 1.75rem 1.25rem 3rem; }}
    h2 {{
      font-size: 1.25rem;
      margin: 2rem 0 0.75rem;
      border-bottom: 2px solid var(--border);
      padding-bottom: 0.35rem;
    }}
    h3 {{ font-size: 1.05rem; margin: 1.25rem 0 0.4rem; }}
    p, li {{ font-size: 1rem; }}
    a {{ color: var(--link); }}
    a:hover {{ color: var(--accent); }}
    .callout {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.15rem;
      margin: 1rem 0;
    }}
    .callout.warn {{
      border-left: 4px solid var(--accent);
    }}
    .map-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 0.85rem;
    }}
    .map-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.9rem 1.1rem;
    }}
    .map-title {{
      font-weight: 600;
      font-size: 1.05rem;
      text-decoration: none;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .map-title:hover {{ text-decoration: underline; }}
    .map-blurb {{ margin: 0.4rem 0 0.35rem; color: var(--muted); }}
    .map-file {{ margin: 0; font-size: 0.85rem; color: var(--muted); }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.85em;
      background: #efece6;
      padding: 0.1em 0.35em;
      border-radius: 4px;
    }}
    table.meta {{
      width: 100%;
      border-collapse: collapse;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.92rem;
      background: var(--card);
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid var(--border);
    }}
    table.meta th, table.meta td {{
      text-align: left;
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    table.meta th {{ width: 32%; color: var(--muted); font-weight: 600; }}
    footer {{
      padding: 1.5rem 1.25rem 2.5rem;
      color: var(--muted);
      font-size: 0.9rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .src-list {{ padding-left: 1.2rem; }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>US Amateur Radio License Geography</h1>
      <p class="tagline">
        Where hams live, how dense they are, where licenses are growing, and how that
        lines up with county age — from public FCC and Census data.
      </p>
      <p class="meta-pill">Site built {generated} · ~{escape(active_s)} active licenses in analysis</p>
    </div>
  </header>

  <main class="wrap">
    <section class="callout warn">
      <strong>Read this first.</strong>
      <p>The FCC may still label a license “Active” after it expires. Our counts
      exclude expired licenses, including those in the two-year renewal grace period,
      unless the data supports continued operating authority through a timely pending
      renewal. The grace period allows renewal but does not itself authorize transmitting.
      Unresolved cases are excluded and listed for review.
      <a href="#license-counting">See which licenses are counted below.</a></p>
      <p>Map geography is the <em>mailing address on the
      FCC license</em>, not proven station location (PO boxes, clubs, and stale addresses
      matter). Growth means <em>new grant dates</em> in the last {growth} months among
      currently active licenses — not net change after expirations. FCC public dumps
      do <em>not</em> include licensee ages; age maps describe the whole county population.</p>
    </section>

    <h2>Data freshness</h2>
    <table class="meta">
      <tr><th>This site generated</th><td>{generated}</td></tr>
      <tr><th>FCC ULS amateur dump downloaded</th><td>{fcc_when}</td></tr>
      <tr><th>Census population estimates downloaded</th><td>{pop_when} · {escape(pop.get("vintage", ""))}</td></tr>
      <tr><th>Census age/sex estimates downloaded</th><td>{age_when} · {escape(age.get("vintage", ""))}</td></tr>
      <tr><th>Growth window</th><td>Last {growth} months (grant date)</td></tr>
      <tr><th>Active licenses loaded</th><td>{escape(active_s)}</td></tr>
    </table>
    <p style="color:var(--muted);font-size:0.95rem;">
      Dates above are when this build’s pipeline downloaded or wrote each artifact
      (UTC file timestamps). The FCC publishes a weekly complete amateur license file;
      this project is intended to refresh monthly via GitHub Actions.
    </p>

    <h2 id="license-counting">Which licenses are counted?</h2>
    <p>Classification date: {escape(classification.get("as_of", "unavailable"))}.
      Active counts include unexpired licenses and expired-date licenses with a
      supported timely renewal still pending, including applications returned for correction.
      A two-year renewal grace period alone does not confer operating privileges.</p>
    <table class="meta">{classification_rows}</table>
    <p>Renewals are matched by call sign and FCC registration number. The documented
      2025–26 renewal deadline extension is included. Other extensions, missing data,
      and ambiguous renewals may need review; these counts do not resolve every
      individual licensing case. Status tables use FCC mailing states; map states
      follow ZIP-to-county placement.</p>

    <h2>Maps</h2>
    <p>
      Continuous-scale county maps clip colors to the bulk of the distribution so
      extreme small-population rates do not flatten everything else. Quantile maps
      put ~equal numbers of counties in each color class — better for comparing ranks
      like 285 vs 360 licenses per 100k. Open any map, then pan/zoom (MapLibre).
    </p>
    {"".join(sections_html)}

    <h2>Downloadable tables</h2>
    {table_links}

    <h2>Data sources</h2>
    <ul class="src-list">
      <li>
        <strong>FCC ULS</strong> —
        <a href="{escape(fcc["url"])}">Amateur complete license dump</a>
        ({escape(fcc["file"])}), plus
        <a href="{FCC_AMAT_APPLICATION_URL}">amateur applications</a>
        ({FCC_AMAT_APPLICATION_ZIP_NAME}). {escape(fcc["note"])}
      </li>
      <li>
        <strong>Census Population Estimates</strong> —
        <a href="{escape(pop["url"])}">co-est2024-alldata</a>
        ({escape(pop.get("vintage", ""))}).
      </li>
      <li>
        <strong>Census age/sex</strong> —
        <a href="{escape(age["url"])}">cc-est2024-agesex-all</a>
        (median age, population 65+).
      </li>
      <li>
        <strong>Boundaries</strong> —
        Census cartographic boundary files ({escape(meta["sources"]["census_boundaries"]["vintage"])}).
      </li>
      <li>
        <strong>ZIP → county</strong> —
        Census 2020 ZCTA–county relationship (primary county by land area).
      </li>
    </ul>

    <h2>Methods (short)</h2>
    <div class="callout">
      <ul>
        <li>Keep unexpired A records and supported timely pending renewals; one row per call sign.</li>
        <li>Join licensee ZIP to county via ZCTA relationship file.</li>
        <li>Per-capita rates use Census county/state population estimates.</li>
        <li>“New grants” = grant_date within the configured rolling window.</li>
        <li>Age correlation is ecological (county vs county), not individual hams.</li>
      </ul>
    </div>

    <h2>Project</h2>
    <p>
      Analysis code and pipeline live in the GitHub repository for this site.
      Rebuild locally with <code>ham all</code> then <code>ham site</code>, or wait for
      the monthly Actions workflow that publishes this Pages site.
    </p>
  </main>

  <footer>
    <div class="wrap">
      Public FCC and Census data · Not affiliated with the FCC or ARRL ·
      Machine-generated pages; verify critical figures against primary sources.
    </div>
  </footer>
</body>
</html>
"""
