"""CLI entrypoint: `ham download|load|geo|aggregate|map|all`."""

from __future__ import annotations

from datetime import datetime, timezone

import typer

from ham_analysis.config import DEFAULT_GROWTH_MONTHS, ensure_dirs

app = typer.Typer(
    name="ham",
    help="Amateur radio geographic analysis (FCC ULS + Census).",
    add_completion=False,
    no_args_is_help=True,
)


@app.command("download-fcc")
def download_fcc_cmd(
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
) -> None:
    """Download and extract FCC amateur license and application dumps."""
    from ham_analysis.download_fcc import download_fcc

    download_fcc(force=force)


@app.command("download-census")
def download_census_cmd(
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
) -> None:
    """Download ACS population, boundaries, and ZIP→county crosswalk."""
    from ham_analysis.download_census import download_census

    download_census(force=force)


@app.command("download")
def download_cmd(
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
) -> None:
    """Download FCC ULS + Census assets."""
    from ham_analysis.download_census import download_census
    from ham_analysis.download_fcc import download_fcc

    download_fcc(force=force)
    download_census(force=force)


@app.command("load")
def load_cmd(
    force: bool = typer.Option(False, "--force", help="Rebuild licenses table"),
) -> None:
    """Parse ULS HD/EN/AM into licenses.parquet."""
    from ham_analysis.load_uls import load_uls

    load_uls(force=force)


@app.command("geo")
def geo_cmd(
    force: bool = typer.Option(False, "--force", help="Rebuild geo join"),
) -> None:
    """Join licenses to county FIPS and population."""
    from ham_analysis.geo_join import geo_join

    geo_join(force=force)


@app.command("aggregate")
def aggregate_cmd(
    months: int = typer.Option(
        DEFAULT_GROWTH_MONTHS,
        "--months",
        "-m",
        help="Growth window: new grants in the last N months",
    ),
    force: bool = typer.Option(False, "--force", help="Rebuild metrics"),
) -> None:
    """Compute state/county metrics (counts, density, growth)."""
    from ham_analysis.aggregate import aggregate

    aggregate(months=months, force=force)


@app.command("map")
def map_cmd(
    months: int = typer.Option(
        DEFAULT_GROWTH_MONTHS,
        "--months",
        "-m",
        help="Label growth maps with this window (should match aggregate)",
    ),
) -> None:
    """Write interactive HTML choropleth maps under outputs/maps/."""
    from ham_analysis.maps import make_maps

    make_maps(months=months)


@app.command("age")
def age_cmd(
    min_population: int = typer.Option(
        1000,
        "--min-population",
        help="Exclude counties smaller than this from correlation/scatter",
    ),
) -> None:
    """
    Correlate county license density with county age (median age, % 65+).

    FCC does not publish licensee ages — this is an ecological (county-level)
    test of whether older places have more hams per capita.
    """
    from ham_analysis.analyze_age import analyze_age

    analyze_age(min_population=min_population)


@app.command("site")
def site_cmd(
    months: int = typer.Option(
        DEFAULT_GROWTH_MONTHS,
        "--months",
        "-m",
        help="Growth window label shown on the landing page",
    ),
) -> None:
    """Build outputs/site/ (index + maps + tables) for GitHub Pages."""
    from ham_analysis.site import build_site

    build_site(growth_months=months)


@app.command("all")
def all_cmd(
    months: int = typer.Option(
        DEFAULT_GROWTH_MONTHS,
        "--months",
        "-m",
        help="Growth window in months",
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-download and rebuild everything"
    ),
) -> None:
    """Run the full pipeline: download → load → geo → aggregate → map → age → site."""
    ensure_dirs()
    from ham_analysis.aggregate import aggregate
    from ham_analysis.analyze_age import analyze_age
    from ham_analysis.download_census import download_census
    from ham_analysis.download_fcc import download_fcc
    from ham_analysis.geo_join import geo_join
    from ham_analysis.load_uls import load_uls
    from ham_analysis.maps import make_maps
    from ham_analysis.site import build_site

    typer.echo("=== 1/7 Download ===")
    download_fcc(force=force)
    download_census(force=force)
    typer.echo("=== 2/7 Load ULS ===")
    as_of = datetime.now(timezone.utc).date()
    load_uls(force=force, as_of=as_of)
    typer.echo("=== 3/7 Geo join ===")
    geo_join(force=True)
    typer.echo("=== 4/7 Aggregate ===")
    aggregate(months=months, force=True, as_of=as_of)
    typer.echo("=== 5/7 Maps ===")
    make_maps(months=months)
    typer.echo("=== 6/7 Age correlation ===")
    analyze_age()
    typer.echo("=== 7/7 Site (GitHub Pages) ===")
    build_site(growth_months=months)
    typer.echo("Done. See outputs/site/ (open outputs/site/index.html)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
