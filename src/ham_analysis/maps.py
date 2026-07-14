"""Interactive choropleth maps (Plotly HTML).

Performance notes
-----------------
County maps include ~3,000 polygons. Plotly's classic ``px.choropleth`` uses an
SVG geo projection: every zoom/pan reprojects every path, which feels laggy and
produces ~20–25 MB HTML files with full Census 500k boundaries.

We instead:
  1. Simplify geometries (Douglas–Peucker) for national viewing.
  2. Quantize coordinates to cut GeoJSON size.
  3. Render with ``px.choropleth_map`` (MapLibre / WebGL), so zoom/pan is
     handled by the tile map engine rather than re-drawing SVG polygons.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
from shapely import set_precision

from ham_analysis.config import (
    CENSUS_RAW_DIR,
    DEFAULT_GROWTH_MONTHS,
    METRICS_COUNTY_PARQUET,
    METRICS_STATE_PARQUET,
    OUTPUT_MAPS,
    ensure_dirs,
)
from ham_analysis.download_census import find_shp

# Contiguous US + AK/HI (exclude territories for cleaner national maps)
STATE_FIPS_KEEP = {
    f"{i:02d}"
    for i in (
        1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23,
        24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
        42, 44, 45, 46, 47, 48, 49, 50, 51, 53, 54, 55, 56,
    )
}

# ~0.01° ≈ 1 km — plenty for national choropleths; keeps coastlines recognizable
SIMPLIFY_TOLERANCE_DEG = 0.01
# Round coords to ~100 m (0.001°) after simplify
COORD_GRID_DEG = 0.001

# Color scale: clip to percentiles so a few extreme counties (often tiny rural
# populations with high rates) don't wash out mid-range differences.
# Hover always shows the true value; values above the high percentile share the
# top color (and below low share the bottom color).
COLOR_SCALE_LOW_PCT = 2.0
COLOR_SCALE_HIGH_PCT = 98.0

# Discrete quantile maps: equal-count bins so mid-range ranks stay visible
QUANTILE_BINS = 5


def _load_metrics() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not METRICS_STATE_PARQUET.exists() or not METRICS_COUNTY_PARQUET.exists():
        raise FileNotFoundError(
            "Missing metrics parquet files. Run `ham aggregate` first."
        )
    state = pd.read_parquet(METRICS_STATE_PARQUET)
    county = pd.read_parquet(METRICS_COUNTY_PARQUET)
    state["state_fips"] = state["state_fips"].astype(str).str.zfill(2)
    county["county_fips"] = county["county_fips"].astype(str).str.zfill(5)
    county["state_fips"] = county["state_fips"].astype(str).str.zfill(2)
    return state, county


def _load_boundaries() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    county_dir = CENSUS_RAW_DIR / "cb_county"
    state_dir = CENSUS_RAW_DIR / "cb_state"
    county_shp = find_shp(county_dir, "county")
    state_shp = find_shp(state_dir, "state")
    counties = gpd.read_file(county_shp)
    states = gpd.read_file(state_shp)
    counties["GEOID"] = counties["GEOID"].astype(str).str.zfill(5)
    states["GEOID"] = states["GEOID"].astype(str).str.zfill(2)
    return counties, states


def _prepare_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project, simplify, and quantize polygons for smaller/faster maps."""
    gdf = gdf.copy().to_crs(epsg=4326)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    # Simplify first (big vertex reduction), then snap coords to a grid
    gdf["geometry"] = gdf.geometry.simplify(
        SIMPLIFY_TOLERANCE_DEG, preserve_topology=True
    )
    gdf["geometry"] = set_precision(gdf.geometry, grid_size=COORD_GRID_DEG)
    # Drop any empties created by over-simplification
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf.reset_index(drop=True)


def _color_range(
    series: pd.Series,
    *,
    low_pct: float = COLOR_SCALE_LOW_PCT,
    high_pct: float = COLOR_SCALE_HIGH_PCT,
    floor_at_zero: bool = True,
) -> tuple[float, float] | None:
    """
    Robust color limits from percentiles.

    Extreme high rates are usually small-population counties (e.g. Alpine CA
    ~4000 per 100k with 46 licenses). Mapping 0→max linearly makes typical
    counties (median ~250) almost the same color.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 10:
        return None
    lo = float(s.quantile(low_pct / 100.0))
    hi = float(s.quantile(high_pct / 100.0))
    if floor_at_zero:
        lo = max(0.0, lo)
    if not (hi > lo):
        return None
    return (lo, hi)


def _choropleth(
    gdf: gpd.GeoDataFrame,
    *,
    id_col: str,
    color: str,
    title: str,
    hover_name: str,
    hover_data: list[str],
    color_label: str,
    outfile: Path,
    clip_color_scale: bool = True,
) -> Path:
    gdf = _prepare_geometries(gdf)
    gdf[color] = pd.to_numeric(gdf[color], errors="coerce").fillna(0)
    gdf[id_col] = gdf[id_col].astype(str)

    # GeoJSON with GEOID in properties — MapLibre matches via featureidkey
    geojson = json.loads(gdf[[id_col, "geometry"]].to_json())
    plot_df = pd.DataFrame(gdf.drop(columns="geometry"))

    hover_map: dict = {c: True for c in hover_data if c in plot_df.columns}
    hover_map[id_col] = False

    range_color = None
    scale_note = ""
    if clip_color_scale:
        # Absolute counts: keep 0 as the floor so "few" still reads light.
        # Rates/percentages: clip both tails so mid-range contrast is preserved.
        if color in {"license_count", "new_grants"}:
            range_color = _color_range(
                plot_df[color], low_pct=0.0, high_pct=COLOR_SCALE_HIGH_PCT
            )
            if range_color is not None:
                range_color = (0.0, range_color[1])
        else:
            range_color = _color_range(plot_df[color])
        if range_color is not None:
            lo, hi = range_color
            # Pretty-print: integers for large counts, 1 decimal for rates
            if hi >= 100:
                lo_s, hi_s = f"{lo:.0f}", f"{hi:.0f}"
            else:
                lo_s, hi_s = f"{lo:.1f}", f"{hi:.1f}"
            scale_note = (
                f" Color scale clipped to {lo_s}–{hi_s} "
                f"({COLOR_SCALE_LOW_PCT:.0f}th–{COLOR_SCALE_HIGH_PCT:.0f}th pctile); "
                "hover shows actual values."
            )
            print(f"    color scale [{lo_s}, {hi_s}] for {color}")

    fig = px.choropleth_map(
        plot_df,
        geojson=geojson,
        locations=id_col,
        featureidkey=f"properties.{id_col}",
        color=color,
        hover_name=hover_name if hover_name in plot_df.columns else None,
        hover_data=hover_map,
        color_continuous_scale="YlOrRd",
        range_color=list(range_color) if range_color else None,
        title=title,
        labels={color: color_label},
        # Contiguous US framing; users can pan to AK/HI
        center={"lat": 39.5, "lon": -98.35},
        zoom=3.2,
        map_style="carto-positron",
        opacity=0.75,
        height=700,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=50, b=50),
        coloraxis_colorbar=dict(
            title=color_label,
            # Make clear the bar is not the full data range when clipped
            len=0.75,
        ),
        # Keep map interaction snappy; avoid expensive transitions
        transition_duration=0,
        annotations=[
            dict(
                text=(
                    "Geography from FCC mailing address on license — "
                    "not necessarily station location. "
                    "Boundaries simplified for performance."
                    + scale_note
                ),
                x=0.5,
                y=-0.04,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=10, color="gray"),
            )
        ],
    )
    # MapLibre config: fewer redraw thrash points
    fig.update_traces(marker_line_width=0.2, marker_line_color="rgba(80,80,80,0.4)")

    outfile.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        str(outfile),
        include_plotlyjs="cdn",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    size_mb = outfile.stat().st_size / (1024 * 1024)
    print(f"  → {outfile} ({size_mb:.1f} MB)")
    return outfile


def _fmt_edge(v: float) -> str:
    if abs(v) >= 100 or float(v).is_integer():
        return f"{v:.0f}"
    return f"{v:.1f}"


def _assign_quantile_bins(
    series: pd.Series, *, n_bins: int = QUANTILE_BINS
) -> tuple[pd.Series, list[str]]:
    """
    Equal-count quantile bins with human-readable labels including value ranges.

    Returns (bin_label series, ordered list of labels low→high).
    """
    s = pd.to_numeric(series, errors="coerce")
    # qcut needs unique bin edges; drop duplicate edges if many ties
    cats = pd.qcut(s, q=n_bins, duplicates="drop")
    # Rebuild labels from intervals so legend shows actual cut points
    intervals = cats.cat.categories
    labels: list[str] = []
    for i, iv in enumerate(intervals, start=1):
        lo, hi = float(iv.left), float(iv.right)
        # qcut edges can be slightly below 0 for non-negative rates; clamp display
        if lo < 0 and hi >= 0:
            lo = 0.0
        labels.append(f"Q{i}: {_fmt_edge(lo)} – {_fmt_edge(hi)}")
    label_map = dict(zip(intervals, labels, strict=True))
    out = cats.map(label_map)
    # Preserve category order for legend
    ordered = pd.Categorical(out, categories=labels, ordered=True)
    return pd.Series(ordered, index=series.index), labels


def _choropleth_quantile(
    gdf: gpd.GeoDataFrame,
    *,
    id_col: str,
    color: str,
    title: str,
    hover_name: str,
    hover_data: list[str],
    color_label: str,
    outfile: Path,
    n_bins: int = QUANTILE_BINS,
) -> Path:
    """Choropleth with equal-count quantile bins (discrete legend)."""
    gdf = _prepare_geometries(gdf)
    gdf[color] = pd.to_numeric(gdf[color], errors="coerce").fillna(0)
    gdf[id_col] = gdf[id_col].astype(str)

    geojson = json.loads(gdf[[id_col, "geometry"]].to_json())
    plot_df = pd.DataFrame(gdf.drop(columns="geometry"))

    bin_col = "_quantile_bin"
    plot_df[bin_col], ordered_labels = _assign_quantile_bins(
        plot_df[color], n_bins=n_bins
    )
    n_actual = len(ordered_labels)
    print(f"    quantile bins ({n_actual}) for {color}: {ordered_labels}")

    hover_map: dict = {c: True for c in hover_data if c in plot_df.columns}
    hover_map[id_col] = False
    hover_map[bin_col] = True

    # YlOrRd discrete: sample n_actual colors from sequential scale
    palette = px.colors.sample_colorscale(
        "YlOrRd", [i / max(n_actual - 1, 1) for i in range(n_actual)]
    )

    fig = px.choropleth_map(
        plot_df,
        geojson=geojson,
        locations=id_col,
        featureidkey=f"properties.{id_col}",
        color=bin_col,
        hover_name=hover_name if hover_name in plot_df.columns else None,
        hover_data=hover_map,
        color_discrete_sequence=palette,
        category_orders={bin_col: ordered_labels},
        title=title,
        labels={bin_col: color_label, color: color_label},
        center={"lat": 39.5, "lon": -98.35},
        zoom=3.2,
        map_style="carto-positron",
        opacity=0.75,
        height=700,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=50, b=50),
        legend=dict(
            title=dict(text=f"{color_label} (quantiles)"),
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            bgcolor="rgba(255,255,255,0.85)",
        ),
        transition_duration=0,
        annotations=[
            dict(
                text=(
                    "Equal-count quantile bins: each class has ~the same number of "
                    f"counties ({n_actual} classes). "
                    "Hover shows the actual value. "
                    "Geography is FCC mailing address; boundaries simplified."
                ),
                x=0.5,
                y=-0.04,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=10, color="gray"),
            )
        ],
    )
    fig.update_traces(marker_line_width=0.2, marker_line_color="rgba(80,80,80,0.4)")

    outfile.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        str(outfile),
        include_plotlyjs="cdn",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    size_mb = outfile.stat().st_size / (1024 * 1024)
    print(f"  → {outfile} ({size_mb:.1f} MB)")
    return outfile


def make_maps(*, months: int = DEFAULT_GROWTH_MONTHS) -> list[Path]:
    """Build state and county choropleths for count, density, and growth."""
    ensure_dirs()
    print("  Building maps (simplified geometries + MapLibre for faster zoom)")
    state_m, county_m = _load_metrics()
    counties_g, states_g = _load_boundaries()

    states = states_g.merge(
        state_m, left_on="GEOID", right_on="state_fips", how="inner"
    )
    counties = counties_g.merge(
        county_m, left_on="GEOID", right_on="county_fips", how="left"
    )

    states = states[states["GEOID"].isin(STATE_FIPS_KEEP)].copy()
    counties = counties[counties["STATEFP"].isin(STATE_FIPS_KEEP)].copy()

    states["label"] = states["NAME"]
    if "state" in counties.columns:
        counties["label"] = counties.apply(
            lambda r: (
                f"{r['NAME']}, {r['state']}"
                if pd.notna(r.get("state"))
                else str(r["NAME"])
            ),
            axis=1,
        )
    else:
        counties["label"] = counties["NAME"]

    written: list[Path] = []

    specs: list[tuple[gpd.GeoDataFrame, str, str, str, list[str], str, str]] = [
        (
            states,
            "license_count",
            "Active Amateur Licenses by State",
            "label",
            ["license_count", "licenses_per_100k", "new_grants", "population"],
            "Licenses",
            "state_license_count.html",
        ),
        (
            states,
            "licenses_per_100k",
            "Amateur Licenses per 100,000 Residents (State)",
            "label",
            ["license_count", "licenses_per_100k", "population"],
            "Per 100k",
            "state_licenses_per_100k.html",
        ),
        (
            states,
            "new_grants",
            f"New License Grants — Last {months} Months (State)",
            "label",
            ["new_grants", "new_grants_per_100k", "license_count"],
            "New grants",
            "state_new_grants.html",
        ),
        (
            states,
            "new_grants_per_100k",
            f"New Grants per 100,000 Residents — Last {months} Months (State)",
            "label",
            ["new_grants", "new_grants_per_100k", "license_count", "population"],
            "New per 100k",
            "state_new_grants_per_100k.html",
        ),
        (
            counties,
            "license_count",
            "Active Amateur Licenses by County",
            "label",
            ["license_count", "licenses_per_100k", "new_grants", "population"],
            "Licenses",
            "county_license_count.html",
        ),
        (
            counties,
            "licenses_per_100k",
            "Amateur Licenses per 100,000 Residents (County)",
            "label",
            ["license_count", "licenses_per_100k", "population"],
            "Per 100k",
            "county_licenses_per_100k.html",
        ),
        (
            counties,
            "new_grants",
            f"New License Grants — Last {months} Months (County)",
            "label",
            ["new_grants", "new_grants_per_100k", "license_count"],
            "New grants",
            "county_new_grants.html",
        ),
        (
            counties,
            "new_grants_per_100k",
            f"New Grants per 100,000 Residents — Last {months} Months (County)",
            "label",
            ["new_grants", "new_grants_per_100k", "population"],
            "New per 100k",
            "county_new_grants_per_100k.html",
        ),
    ]

    if "median_age" in county_m.columns and county_m["median_age"].notna().any():
        specs.extend(
            [
                (
                    counties,
                    "median_age",
                    "County Median Age (Census Population Estimates)",
                    "label",
                    ["median_age", "pct_65plus", "licenses_per_100k", "population"],
                    "Median age",
                    "county_median_age.html",
                ),
                (
                    counties,
                    "pct_65plus",
                    "Share of Population Age 65+ (%)",
                    "label",
                    ["pct_65plus", "median_age", "licenses_per_100k", "population"],
                    "% 65+",
                    "county_pct_65plus.html",
                ),
            ]
        )

    for gdf, color, title, hover_name, hover_data, color_label, fname in specs:
        written.append(
            _choropleth(
                gdf,
                id_col="GEOID",
                color=color,
                title=title,
                hover_name=hover_name,
                hover_data=hover_data,
                color_label=color_label,
                outfile=OUTPUT_MAPS / fname,
            )
        )

    # Quantile-binned companions — equal-count classes so mid-range differences
    # stay visible without outlier-driven continuous scales.
    quantile_specs: list[tuple[gpd.GeoDataFrame, str, str, str, list[str], str, str]] = [
        (
            counties,
            "licenses_per_100k",
            "Amateur Licenses per 100,000 Residents (County, quintiles)",
            "label",
            ["license_count", "licenses_per_100k", "population"],
            "Per 100k",
            "county_licenses_per_100k_quantile.html",
        ),
        (
            counties,
            "new_grants_per_100k",
            f"New Grants per 100,000 Residents — Last {months} Months (County, quintiles)",
            "label",
            ["new_grants", "new_grants_per_100k", "population"],
            "New per 100k",
            "county_new_grants_per_100k_quantile.html",
        ),
    ]
    if "pct_65plus" in county_m.columns and county_m["pct_65plus"].notna().any():
        quantile_specs.append(
            (
                counties,
                "pct_65plus",
                "Share of Population Age 65+ (County, quintiles)",
                "label",
                ["pct_65plus", "median_age", "licenses_per_100k", "population"],
                "% 65+",
                "county_pct_65plus_quantile.html",
            )
        )
    print("  Building quantile-binned county maps")
    for gdf, color, title, hover_name, hover_data, color_label, fname in quantile_specs:
        written.append(
            _choropleth_quantile(
                gdf,
                id_col="GEOID",
                color=color,
                title=title,
                hover_name=hover_name,
                hover_data=hover_data,
                color_label=color_label,
                outfile=OUTPUT_MAPS / fname,
            )
        )

    return written
