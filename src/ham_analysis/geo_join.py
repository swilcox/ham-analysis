"""Join licenses to county FIPS via ZIP crosswalk and attach population."""

from __future__ import annotations

from pathlib import Path

import duckdb

from ham_analysis.config import (
    CENSUS_RAW_DIR,
    DUCKDB_PATH,
    EXTERNAL_DIR,
    GEO_LICENSES_PARQUET,
    LICENSES_PARQUET,
    ensure_dirs,
    state_fips_lookup_sql,
)


def geo_join(*, force: bool = False) -> Path:
    """
    Attach county_fips / state_fips to each license using ZCTA→county map.

    ZIPs that don't match a ZCTA are left with null county_fips; state from
    the FCC address is retained either way.
    """
    ensure_dirs()
    if (GEO_LICENSES_PARQUET.exists() and LICENSES_PARQUET.exists() and not force
            and GEO_LICENSES_PARQUET.stat().st_mtime_ns >= LICENSES_PARQUET.stat().st_mtime_ns):
        print(f"  Using cached {GEO_LICENSES_PARQUET}")
        return GEO_LICENSES_PARQUET

    if not LICENSES_PARQUET.exists():
        raise FileNotFoundError(
            f"Missing {LICENSES_PARQUET}. Run `ham load` first."
        )

    zip_path = EXTERNAL_DIR / "zip_to_county.csv"
    if not zip_path.exists():
        raise FileNotFoundError(
            f"Missing {zip_path}. Run `ham download` (census) first."
        )

    state_pop = CENSUS_RAW_DIR / "acs_population_state.csv"
    county_pop = CENSUS_RAW_DIR / "acs_population_county.csv"
    if not state_pop.exists() or not county_pop.exists():
        raise FileNotFoundError(
            "Missing ACS population CSVs. Run `ham download` first."
        )

    print("  Joining licenses → ZIP → county + population")
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE licenses AS
            SELECT * FROM read_parquet('{_sql(LICENSES_PARQUET)}')
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE zip_to_county AS
            SELECT
                zcta AS zip5,
                county_fips,
                state_fips AS crosswalk_state_fips,
                CAST(county_parts AS INTEGER) AS county_parts
            FROM read_csv_auto('{_sql(zip_path)}', header=true, types={{
                'zcta': 'VARCHAR',
                'county_fips': 'VARCHAR',
                'state_fips': 'VARCHAR',
                'county_parts': 'VARCHAR'
            }})
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE pop_state AS
            SELECT state_fips, name AS state_name, CAST(population AS BIGINT) AS population
            FROM read_csv_auto('{_sql(state_pop)}', header=true)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE pop_county AS
            SELECT
                county_fips,
                state_fips,
                name AS county_name,
                CAST(population AS BIGINT) AS population
            FROM read_csv_auto('{_sql(county_pop)}', header=true)
            """
        )

        # State postal → FIPS for licenses that only have FCC state (no ZIP match)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE state_fips_lookup AS
            SELECT * FROM (VALUES
                {state_fips_lookup_sql()}
            ) AS t(state, state_fips)
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TABLE geo_licenses AS
            SELECT
                l.*,
                z.county_fips,
                COALESCE(z.crosswalk_state_fips, s.state_fips) AS state_fips,
                z.county_parts,
                pc.county_name,
                pc.population AS county_population,
                ps.state_name,
                ps.population AS state_population
            FROM licenses l
            LEFT JOIN zip_to_county z ON l.zip5 = z.zip5
            LEFT JOIN state_fips_lookup s ON l.state = s.state
            LEFT JOIN pop_county pc ON z.county_fips = pc.county_fips
            LEFT JOIN pop_state ps
                ON COALESCE(z.crosswalk_state_fips, s.state_fips) = ps.state_fips
            """
        )

        stats = con.execute(
            """
            SELECT
                COUNT(*) AS n,
                COUNT(county_fips) AS with_county,
                COUNT(state_fips) AS with_state_fips
            FROM geo_licenses
            """
        ).fetchone()
        print(
            f"  Rows: {stats[0]:,} | with county: {stats[1]:,} | "
            f"with state FIPS: {stats[2]:,}"
        )

        con.execute(
            f"COPY geo_licenses TO '{_sql(GEO_LICENSES_PARQUET)}' (FORMAT PARQUET)"
        )
        print(f"  → {GEO_LICENSES_PARQUET}")
    finally:
        con.close()

    return GEO_LICENSES_PARQUET


def _sql(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")
