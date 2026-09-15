"""Build state and county metrics tables (counts, density, growth, class mix)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from ham_analysis.config import (
    CENSUS_RAW_DIR,
    COUNTY_AGE_CSV,
    DEFAULT_GROWTH_MONTHS,
    DUCKDB_PATH,
    GEO_LICENSES_PARQUET,
    METRICS_COUNTY_CSV,
    METRICS_COUNTY_PARQUET,
    METRICS_STATE_CSV,
    METRICS_STATE_PARQUET,
    ensure_dirs,
    state_fips_lookup_sql,
)


def aggregate(
    *,
    months: int = DEFAULT_GROWTH_MONTHS,
    force: bool = False,
    as_of: date | None = None,
) -> tuple[Path, Path]:
    """
    Aggregate geo_licenses into state and county metrics.

    Growth heuristic: active licenses whose grant_date falls within the last
    `months` months (relative to `as_of`, default today). This proxies new
    licensees, not net stock change from expirations/cancellations.
    """
    ensure_dirs()
    if (
        METRICS_STATE_PARQUET.exists()
        and METRICS_COUNTY_PARQUET.exists()
        and GEO_LICENSES_PARQUET.exists()
        and min(METRICS_STATE_PARQUET.stat().st_mtime_ns,
                METRICS_COUNTY_PARQUET.stat().st_mtime_ns) >= GEO_LICENSES_PARQUET.stat().st_mtime_ns
        and not force
    ):
        print(f"  Using cached metrics (delete or pass --force to rebuild)")
        return METRICS_STATE_PARQUET, METRICS_COUNTY_PARQUET

    if not GEO_LICENSES_PARQUET.exists():
        raise FileNotFoundError(
            f"Missing {GEO_LICENSES_PARQUET}. Run `ham build` / `ham geo` first."
        )

    as_of = as_of or date.today()
    print(f"  Aggregating metrics (growth window: {months} months as of {as_of})")

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE geo_licenses AS
            SELECT * FROM read_parquet('{_sql(GEO_LICENSES_PARQUET)}')
            """
        )
        # Postal labels come from state_fips only — never from FCC address state.
        # (A minority of licenses have state/ZIP disagreements; ANY_VALUE(state)
        # could label e.g. Pend Oreille WA as CA.)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE state_fips_lookup AS
            SELECT * FROM (VALUES
                {state_fips_lookup_sql()}
            ) AS t(state, state_fips)
            """
        )
        as_of_s = as_of.isoformat()
        m = int(months)

        con.execute(
            f"""
            CREATE OR REPLACE TABLE metrics_state AS
            SELECT
                LPAD(CAST(g.state_fips AS VARCHAR), 2, '0') AS state_fips,
                s.state AS state,
                ANY_VALUE(g.state_name) AS state_name,
                ANY_VALUE(g.state_population) AS population,
                COUNT(*) AS license_count,
                COUNT(*) FILTER (
                    WHERE g.grant_date IS NOT NULL
                      AND g.grant_date > DATE '{as_of_s}' - INTERVAL {m} MONTH
                      AND g.grant_date <= DATE '{as_of_s}'
                ) AS new_grants,
                ROUND(
                    100000.0 * COUNT(*) / NULLIF(ANY_VALUE(g.state_population), 0),
                    2
                ) AS licenses_per_100k,
                ROUND(
                    100000.0 * COUNT(*) FILTER (
                        WHERE g.grant_date IS NOT NULL
                          AND g.grant_date > DATE '{as_of_s}' - INTERVAL {m} MONTH
                          AND g.grant_date <= DATE '{as_of_s}'
                    ) / NULLIF(ANY_VALUE(g.state_population), 0),
                    2
                ) AS new_grants_per_100k,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE g.operator_class = 'T')
                        / NULLIF(COUNT(*), 0),
                    2
                ) AS pct_technician,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE g.operator_class = 'G')
                        / NULLIF(COUNT(*), 0),
                    2
                ) AS pct_general,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE g.operator_class = 'E')
                        / NULLIF(COUNT(*), 0),
                    2
                ) AS pct_extra,
                COUNT(*) FILTER (WHERE g.operator_class = 'T') AS count_technician,
                COUNT(*) FILTER (WHERE g.operator_class = 'G') AS count_general,
                COUNT(*) FILTER (WHERE g.operator_class = 'E') AS count_extra
            FROM geo_licenses g
            LEFT JOIN state_fips_lookup s
                ON LPAD(CAST(g.state_fips AS VARCHAR), 2, '0') = s.state_fips
            WHERE g.state_fips IS NOT NULL
            GROUP BY LPAD(CAST(g.state_fips AS VARCHAR), 2, '0'), s.state
            ORDER BY license_count DESC
            """
        )

        con.execute(
            f"""
            CREATE OR REPLACE TABLE metrics_county AS
            SELECT
                LPAD(CAST(g.county_fips AS VARCHAR), 5, '0') AS county_fips,
                ANY_VALUE(LPAD(CAST(g.state_fips AS VARCHAR), 2, '0')) AS state_fips,
                COUNT(*) AS license_count,
                COUNT(*) FILTER (
                    WHERE g.grant_date IS NOT NULL
                      AND g.grant_date > DATE '{as_of_s}' - INTERVAL {m} MONTH
                      AND g.grant_date <= DATE '{as_of_s}'
                ) AS new_grants,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE g.operator_class = 'E')
                        / NULLIF(COUNT(*), 0),
                    2
                ) AS pct_extra
            FROM geo_licenses g
            WHERE g.county_fips IS NOT NULL
            GROUP BY LPAD(CAST(g.county_fips AS VARCHAR), 5, '0')
            ORDER BY license_count DESC
            """
        )

        county_pop_csv = CENSUS_RAW_DIR / "acs_population_county.csv"
        # Full population frame so counties with 0 hams appear as zero on maps.
        # Optionally join county median age / %65+ (Census PopEst agesex).
        age_join = ""
        age_select = """
                NULL::DOUBLE AS median_age,
                NULL::DOUBLE AS pct_65plus
        """
        if COUNTY_AGE_CSV.exists():
            con.execute(
                f"""
                CREATE OR REPLACE TABLE county_age AS
                SELECT
                    county_fips,
                    CAST(median_age AS DOUBLE) AS median_age,
                    CAST(pct_65plus AS DOUBLE) AS pct_65plus
                FROM read_csv_auto('{_sql(COUNTY_AGE_CSV)}', header=true)
                """
            )
            age_join = "LEFT JOIN county_age a ON p.county_fips = a.county_fips"
            age_select = """
                a.median_age,
                a.pct_65plus
            """
            print("  Joined county age demographics (median age, % 65+)")
        else:
            print(
                "  Note: county age file missing — run `ham download-census` "
                "for age correlation analysis"
            )

        con.execute(
            f"""
            CREATE OR REPLACE TABLE metrics_county_full AS
            SELECT
                p.county_fips,
                p.state_fips,
                p.county_name,
                p.population,
                COALESCE(m.license_count, 0) AS license_count,
                COALESCE(m.new_grants, 0) AS new_grants,
                CASE
                    WHEN p.population > 0
                    THEN ROUND(100000.0 * COALESCE(m.license_count, 0) / p.population, 2)
                    ELSE NULL
                END AS licenses_per_100k,
                CASE
                    WHEN p.population > 0
                    THEN ROUND(100000.0 * COALESCE(m.new_grants, 0) / p.population, 2)
                    ELSE NULL
                END AS new_grants_per_100k,
                m.pct_extra,
                s.state AS state,
                {age_select}
            FROM (
                SELECT
                    LPAD(CAST(county_fips AS VARCHAR), 5, '0') AS county_fips,
                    LPAD(CAST(state_fips AS VARCHAR), 2, '0') AS state_fips,
                    name AS county_name,
                    CAST(population AS BIGINT) AS population
                FROM read_csv_auto('{_sql(county_pop_csv)}', header=true)
            ) p
            LEFT JOIN metrics_county m ON p.county_fips = m.county_fips
            LEFT JOIN state_fips_lookup s ON p.state_fips = s.state_fips
            {age_join}
            """
        )

        # Prefer full county frame for maps
        con.execute("CREATE OR REPLACE TABLE metrics_county AS SELECT * FROM metrics_county_full")

        n_state = con.execute("SELECT COUNT(*) FROM metrics_state").fetchone()[0]
        n_county = con.execute("SELECT COUNT(*) FROM metrics_county").fetchone()[0]
        top = con.execute(
            """
            SELECT state, license_count, licenses_per_100k, new_grants
            FROM metrics_state
            ORDER BY license_count DESC
            LIMIT 5
            """
        ).fetchall()
        print(f"  States: {n_state} | Counties: {n_county}")
        print("  Top states by license count:")
        for row in top:
            print(
                f"    {row[0]}: {row[1]:,} licenses, "
                f"{row[2]} per 100k, {row[3]:,} new grants"
            )

        con.execute(
            f"COPY metrics_state TO '{_sql(METRICS_STATE_PARQUET)}' (FORMAT PARQUET)"
        )
        con.execute(
            f"COPY metrics_county TO '{_sql(METRICS_COUNTY_PARQUET)}' (FORMAT PARQUET)"
        )
        con.execute(
            f"COPY metrics_state TO '{_sql(METRICS_STATE_CSV)}' (HEADER, DELIMITER ',')"
        )
        con.execute(
            f"COPY metrics_county TO '{_sql(METRICS_COUNTY_CSV)}' (HEADER, DELIMITER ',')"
        )
        print(f"  → {METRICS_STATE_CSV}")
        print(f"  → {METRICS_COUNTY_CSV}")
    finally:
        con.close()

    return METRICS_STATE_PARQUET, METRICS_COUNTY_PARQUET


def _sql(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")
