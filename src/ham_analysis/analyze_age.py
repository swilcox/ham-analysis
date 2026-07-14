"""
Correlate county ham density with county age demographics.

Important: FCC ULS public dumps do **not** include licensee date of birth.
We cannot compute the average age of ham license holders. Instead we test an
*ecological* hypothesis: do counties with older populations also have higher
licenses per 100k residents?
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ham_analysis.config import (
    AGE_CORRELATION_CSV,
    AGE_SCATTER_HTML,
    METRICS_COUNTY_PARQUET,
    OUTPUT_MAPS,
    OUTPUT_TABLES,
    ensure_dirs,
)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation without scipy."""
    if len(x) < 2:
        return float("nan")
    rx = pd.Series(x).rank().to_numpy(dtype=float)
    ry = pd.Series(y).rank().to_numpy(dtype=float)
    return _pearson(rx, ry)


def _weighted_pearson(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    w = w.astype(float)
    if w.sum() <= 0:
        return float("nan")
    w = w / w.sum()
    mx = np.sum(w * x)
    my = np.sum(w * y)
    cov = np.sum(w * (x - mx) * (y - my))
    sx = np.sqrt(np.sum(w * (x - mx) ** 2))
    sy = np.sqrt(np.sum(w * (y - my) ** 2))
    if sx <= 0 or sy <= 0:
        return float("nan")
    return float(cov / (sx * sy))


def _corr_row(
    df: pd.DataFrame,
    x: str,
    y: str,
    *,
    weight: str | None = None,
    label: str,
) -> dict:
    cols = [x, y] + ([weight] if weight else [])
    sub = df[cols].dropna()
    if weight:
        sub = sub[sub[weight] > 0]
    n = len(sub)
    if n < 3:
        return {
            "comparison": label,
            "x": x,
            "y": y,
            "n": n,
            "pearson_r": None,
            "spearman_r": None,
            "weighted": bool(weight),
        }

    xv = sub[x].to_numpy(dtype=float)
    yv = sub[y].to_numpy(dtype=float)
    spearman = _spearman(xv, yv)

    if weight:
        pearson = _weighted_pearson(xv, yv, sub[weight].to_numpy(dtype=float))
    else:
        pearson = _pearson(xv, yv)

    return {
        "comparison": label,
        "x": x,
        "y": y,
        "n": n,
        "pearson_r": round(pearson, 4) if pearson == pearson else None,
        "spearman_r": round(spearman, 4) if spearman == spearman else None,
        "weighted": bool(weight),
    }


def analyze_age(*, min_population: int = 1000) -> tuple[Path, Path]:
    """
    Write correlation summary + scatter plot of density vs median age / %65+.

    Returns paths to (correlation CSV, scatter HTML).
    """
    ensure_dirs()
    if not METRICS_COUNTY_PARQUET.exists():
        raise FileNotFoundError(
            f"Missing {METRICS_COUNTY_PARQUET}. Run `ham aggregate` first."
        )

    df = pd.read_parquet(METRICS_COUNTY_PARQUET)
    for col in ("median_age", "pct_65plus", "licenses_per_100k", "population"):
        if col not in df.columns:
            raise FileNotFoundError(
                f"Column {col!r} missing from metrics. "
                "Re-run `ham download-census` then `ham aggregate --force`."
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop tiny counties (noisy rates) for main analysis
    work = df[df["population"].fillna(0) >= min_population].copy()
    work = work[work["median_age"].notna() & work["licenses_per_100k"].notna()]

    rows = [
        _corr_row(
            work,
            "median_age",
            "licenses_per_100k",
            label="median_age vs licenses_per_100k (unweighted)",
        ),
        _corr_row(
            work,
            "median_age",
            "licenses_per_100k",
            weight="population",
            label="median_age vs licenses_per_100k (pop-weighted Pearson)",
        ),
        _corr_row(
            work,
            "pct_65plus",
            "licenses_per_100k",
            label="pct_65plus vs licenses_per_100k (unweighted)",
        ),
        _corr_row(
            work,
            "pct_65plus",
            "licenses_per_100k",
            weight="population",
            label="pct_65plus vs licenses_per_100k (pop-weighted Pearson)",
        ),
        _corr_row(
            work,
            "median_age",
            "new_grants_per_100k",
            label="median_age vs new_grants_per_100k (unweighted)",
        ),
        _corr_row(
            work,
            "pct_65plus",
            "new_grants_per_100k",
            label="pct_65plus vs new_grants_per_100k (unweighted)",
        ),
    ]
    corr_df = pd.DataFrame(rows)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)
    corr_df.to_csv(AGE_CORRELATION_CSV, index=False)
    print(f"  Age correlation (counties with pop ≥ {min_population:,}):")
    for _, r in corr_df.iterrows():
        print(
            f"    {r['comparison']}: "
            f"Pearson r={r['pearson_r']}, Spearman r={r['spearman_r']} (n={r['n']})"
        )
    print(f"  → {AGE_CORRELATION_CSV}")

    # Scatter: median age vs density, sized by population
    plot_df = work.copy()
    plot_df["label"] = plot_df.apply(
        lambda r: (
            f"{r['county_name']}"
            if pd.notna(r.get("county_name"))
            else str(r.get("county_fips", ""))
        ),
        axis=1,
    )
    # Cap marker size for readability
    plot_df["pop_size"] = np.sqrt(plot_df["population"].clip(lower=1))

    fig = px.scatter(
        plot_df,
        x="median_age",
        y="licenses_per_100k",
        size="pop_size",
        size_max=28,
        hover_name="label",
        hover_data={
            "licenses_per_100k": True,
            "median_age": True,
            "pct_65plus": True,
            "license_count": True,
            "population": True,
            "pop_size": False,
            "state": True,
        },
        title=(
            "County Amateur License Density vs Median Age<br>"
            "<sup>Ecological correlation — FCC does not publish licensee ages. "
            "Each point is a county's whole population age profile.</sup>"
        ),
        labels={
            "median_age": "County median age (years)",
            "licenses_per_100k": "Active licenses per 100,000 residents",
        },
        opacity=0.55,
    )

    # OLS trend line
    x = plot_df["median_age"].to_numpy(dtype=float)
    y = plot_df["licenses_per_100k"].to_numpy(dtype=float)
    if len(x) >= 2:
        slope, intercept = np.polyfit(x, y, 1)
        x_line = np.linspace(x.min(), x.max(), 100)
        y_line = slope * x_line + intercept
        r = plot_df["median_age"].corr(plot_df["licenses_per_100k"])
        fig.add_trace(
            go.Scatter(
                x=x_line,
                y=y_line,
                mode="lines",
                name=f"OLS fit (r={r:.3f})",
                line=dict(color="crimson", width=2),
            )
        )

    fig.update_layout(
        margin=dict(l=40, r=20, t=80, b=40),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )
    OUTPUT_MAPS.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(AGE_SCATTER_HTML), include_plotlyjs="cdn")
    print(f"  → {AGE_SCATTER_HTML}")

    # Secondary scatter: %65+
    scatter_65 = OUTPUT_MAPS / "county_density_vs_pct_65plus.html"
    fig2 = px.scatter(
        plot_df,
        x="pct_65plus",
        y="licenses_per_100k",
        size="pop_size",
        size_max=28,
        hover_name="label",
        hover_data={
            "licenses_per_100k": True,
            "pct_65plus": True,
            "median_age": True,
            "license_count": True,
            "population": True,
            "pop_size": False,
            "state": True,
        },
        title=(
            "County Amateur License Density vs Share of Population Age 65+<br>"
            "<sup>Ecological correlation — not the ages of individual licensees.</sup>"
        ),
        labels={
            "pct_65plus": "Population age 65+ (%)",
            "licenses_per_100k": "Active licenses per 100,000 residents",
        },
        opacity=0.55,
    )
    x2 = plot_df["pct_65plus"].to_numpy(dtype=float)
    y2 = plot_df["licenses_per_100k"].to_numpy(dtype=float)
    mask = np.isfinite(x2) & np.isfinite(y2)
    if mask.sum() >= 2:
        slope, intercept = np.polyfit(x2[mask], y2[mask], 1)
        x_line = np.linspace(x2[mask].min(), x2[mask].max(), 100)
        r2 = plot_df["pct_65plus"].corr(plot_df["licenses_per_100k"])
        fig2.add_trace(
            go.Scatter(
                x=x_line,
                y=slope * x_line + intercept,
                mode="lines",
                name=f"OLS fit (r={r2:.3f})",
                line=dict(color="crimson", width=2),
            )
        )
    fig2.update_layout(margin=dict(l=40, r=20, t=80, b=40))
    fig2.write_html(str(scatter_65), include_plotlyjs="cdn")
    print(f"  → {scatter_65}")

    return AGE_CORRELATION_CSV, AGE_SCATTER_HTML
