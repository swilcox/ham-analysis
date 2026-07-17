"""Fixture-based tests for ULS loading and aggregation."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest

from ham_analysis import config
from ham_analysis.aggregate import aggregate
from ham_analysis.geo_join import geo_join
from ham_analysis.load_uls import load_uls

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def pipeline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point config paths at a temp workspace with fixture ULS files."""
    root = tmp_path / "proj"
    raw_fcc = root / "data" / "raw" / "fcc" / "extract"
    raw_census = root / "data" / "raw" / "census"
    external = root / "data" / "external"
    processed = root / "data" / "processed"
    tables = root / "outputs" / "tables"
    maps = root / "outputs" / "maps"
    for p in (raw_fcc, raw_census, external, processed, tables, maps):
        p.mkdir(parents=True)

    for name in ("HD.dat", "EN.dat", "AM.dat"):
        shutil.copy(FIXTURES / name, raw_fcc / name)

    # Minimal ZIP→county crosswalk (ZIPs used in fixtures)
    (external / "zip_to_county.csv").write_text(
        "zcta,county_fips,state_fips,county_parts\n"
        "02108,25025,25,1\n"
        "02139,25017,25,1\n"
        "04101,23005,23,1\n"
        "98101,53033,53,1\n"
        "99156,53051,53,1\n",
        encoding="utf-8",
    )
    (raw_census / "acs_population_state.csv").write_text(
        "name,population,state_fips\n"
        "Massachusetts,7000000,25\n"
        "Maine,1400000,23\n"
        "Washington,7700000,53\n",
        encoding="utf-8",
    )
    (raw_census / "acs_population_county.csv").write_text(
        "name,population,state_fips,county_fips_3,county_fips\n"
        '"Suffolk County, Massachusetts",800000,25,025,25025\n'
        '"Middlesex County, Massachusetts",1600000,25,017,25017\n'
        '"Cumberland County, Maine",300000,23,005,23005\n'
        '"King County, Washington",2300000,53,033,53033\n'
        '"Pend Oreille County, Washington",14000,53,051,53051\n',
        encoding="utf-8",
    )
    (raw_census / "county_age.csv").write_text(
        "county_fips,median_age,pct_65plus,age65plus,population_age\n"
        "25025,34.5,12.0,96000,800000\n"
        "25017,39.0,15.0,240000,1600000\n"
        "23005,42.0,20.0,60000,300000\n"
        "53033,37.0,13.0,299000,2300000\n"
        "53051,49.5,27.0,3800,14000\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", root / "data")
    monkeypatch.setattr(config, "RAW_DIR", root / "data" / "raw")
    monkeypatch.setattr(config, "PROCESSED_DIR", processed)
    monkeypatch.setattr(config, "EXTERNAL_DIR", external)
    monkeypatch.setattr(config, "FCC_RAW_DIR", root / "data" / "raw" / "fcc")
    monkeypatch.setattr(config, "CENSUS_RAW_DIR", raw_census)
    monkeypatch.setattr(config, "OUTPUT_TABLES", tables)
    monkeypatch.setattr(config, "OUTPUT_MAPS", maps)
    monkeypatch.setattr(config, "DUCKDB_PATH", processed / "ham.duckdb")
    monkeypatch.setattr(config, "LICENSES_PARQUET", processed / "licenses.parquet")
    monkeypatch.setattr(config, "GEO_LICENSES_PARQUET", processed / "geo_licenses.parquet")
    monkeypatch.setattr(config, "METRICS_STATE_PARQUET", processed / "metrics_state.parquet")
    monkeypatch.setattr(config, "METRICS_COUNTY_PARQUET", processed / "metrics_county.parquet")
    monkeypatch.setattr(config, "METRICS_STATE_CSV", tables / "metrics_state.csv")
    monkeypatch.setattr(config, "METRICS_COUNTY_CSV", tables / "metrics_county.csv")
    monkeypatch.setattr(config, "COUNTY_AGE_CSV", raw_census / "county_age.csv")
    monkeypatch.setattr(config, "AGE_CORRELATION_CSV", tables / "age_correlation.csv")
    monkeypatch.setattr(
        config, "AGE_SCATTER_HTML", maps / "county_density_vs_median_age.html"
    )

    # Modules imported config values at call time via config.X — also patch load_uls paths
    import ham_analysis.aggregate as agg_mod
    import ham_analysis.analyze_age as age_mod
    import ham_analysis.geo_join as geo_mod
    import ham_analysis.load_uls as load_mod

    for mod in (load_mod, geo_mod, agg_mod, age_mod):
        for attr in (
            "DUCKDB_PATH",
            "LICENSES_PARQUET",
            "GEO_LICENSES_PARQUET",
            "FCC_RAW_DIR",
            "PROCESSED_DIR",
            "EXTERNAL_DIR",
            "CENSUS_RAW_DIR",
            "COUNTY_AGE_CSV",
            "METRICS_STATE_PARQUET",
            "METRICS_COUNTY_PARQUET",
            "METRICS_STATE_CSV",
            "METRICS_COUNTY_CSV",
            "AGE_CORRELATION_CSV",
            "AGE_SCATTER_HTML",
            "OUTPUT_MAPS",
            "OUTPUT_TABLES",
        ):
            if hasattr(config, attr) and hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, getattr(config, attr))

    return root


def test_load_uls_filters_inactive_and_joins(pipeline_env: Path):
    path = load_uls(force=True)
    assert path.exists()

    con = duckdb.connect()
    df = con.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY call_sign").fetchdf()
    # K1CCC is expired (status E) — excluded
    assert set(df["call_sign"]) == {"K1AAA", "K1BBB", "KF6KAL", "W1DDD"}
    assert df.loc[df["call_sign"] == "K1AAA", "operator_class"].iloc[0] == "E"
    assert df.loc[df["call_sign"] == "K1AAA", "state"].iloc[0] == "MA"
    assert df.loc[df["call_sign"] == "K1AAA", "zip5"].iloc[0] == "02108"
    # FCC address state can disagree with ZIP (KF6KAL: CA + 99156 WA)
    assert df.loc[df["call_sign"] == "KF6KAL", "state"].iloc[0] == "CA"
    assert df.loc[df["call_sign"] == "KF6KAL", "zip5"].iloc[0] == "99156"


def test_geo_and_aggregate(pipeline_env: Path):
    load_uls(force=True)
    geo_join(force=True)
    state_p, county_p = aggregate(
        months=12, force=True, as_of=date(2025, 1, 1)
    )
    assert state_p.exists() and county_p.exists()

    con = duckdb.connect()
    states = con.execute(
        f"SELECT * FROM read_parquet('{state_p}') ORDER BY state"
    ).fetchdf()
    # MA has 2 active; WA has W1DDD + KF6KAL (ZIP-placed); ME only had expired
    ma = states[states["state"] == "MA"].iloc[0]
    assert ma["license_count"] == 2
    # Window 2024-01-01..2025-01-01: K1BBB (2024-06-01) counts; K1AAA (2020) does not
    assert ma["new_grants"] == 1
    wa = states[states["state"] == "WA"].iloc[0]
    assert wa["license_count"] == 2
    # W1DDD grant 2023-11-20 and KF6KAL 2022 are outside the 12-month window
    assert wa["new_grants"] == 0

    counties = con.execute(
        f"SELECT * FROM read_parquet('{county_p}') WHERE county_fips = '25025'"
    ).fetchdf()
    assert abs(float(counties.iloc[0]["median_age"]) - 34.5) < 0.01


def test_metrics_state_from_fips_not_fcc_address(pipeline_env: Path):
    """County/state postal labels follow FIPS placement, not FCC state.

    Regression: KF6KAL has FCC state=CA but ZIP 99156 → Pend Oreille, WA.
    Metrics must label the county WA, never CA via ANY_VALUE(state).
    """
    load_uls(force=True)
    geo_join(force=True)
    _, county_p = aggregate(months=12, force=True, as_of=date(2025, 1, 1))

    con = duckdb.connect()
    geo = con.execute(
        "SELECT call_sign, state AS fcc_state, county_fips, state_fips "
        f"FROM read_parquet('{config.GEO_LICENSES_PARQUET}') "
        "WHERE call_sign = 'KF6KAL'"
    ).fetchdf()
    assert len(geo) == 1
    assert geo.iloc[0]["fcc_state"] == "CA"
    assert geo.iloc[0]["county_fips"] == "53051"
    assert geo.iloc[0]["state_fips"] == "53"

    po = con.execute(
        f"SELECT state, state_fips, license_count FROM read_parquet('{county_p}') "
        "WHERE county_fips = '53051'"
    ).fetchdf()
    assert len(po) == 1
    assert po.iloc[0]["state"] == "WA"
    assert po.iloc[0]["state_fips"] == "53"
    assert int(po.iloc[0]["license_count"]) == 1

    # Every metrics county with a state_fips must have matching postal
    from ham_analysis.config import STATE_FIPS_TO_POSTAL

    all_c = con.execute(
        f"SELECT county_fips, state, state_fips FROM read_parquet('{county_p}') "
        "WHERE state_fips IS NOT NULL"
    ).fetchdf()
    for _, row in all_c.iterrows():
        expected = STATE_FIPS_TO_POSTAL.get(str(row["state_fips"]).zfill(2))
        if expected is not None:
            assert row["state"] == expected, (
                f"{row['county_fips']}: state={row['state']!r} "
                f"expected {expected!r} for FIPS {row['state_fips']}"
            )


def test_analyze_age(pipeline_env: Path):
    from ham_analysis.analyze_age import analyze_age

    load_uls(force=True)
    geo_join(force=True)
    aggregate(months=12, force=True, as_of=date(2025, 1, 1))
    corr_path, scatter_path = analyze_age(min_population=100)
    assert corr_path.exists()
    assert scatter_path.exists()
    corr = corr_path.read_text(encoding="utf-8")
    assert "median_age" in corr
    assert "pearson_r" in corr
