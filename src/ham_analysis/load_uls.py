"""Parse FCC ULS HD/EN/AM files into a cleaned licenses table."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from ham_analysis.config import (
    DUCKDB_PATH,
    FCC_RAW_DIR,
    LICENSES_PARQUET,
    PROCESSED_DIR,
    OUTPUT_TABLES,
    ensure_dirs,
)

# Column layouts from FCC Public Access Database Definitions (pipe-delimited).
# We only name columns we need; remaining positions are ignored via SELECT.

HD_COLUMNS = [
    "record_type",
    "unique_system_identifier",
    "uls_file_number",
    "ebf_number",
    "call_sign",
    "license_status",
    "radio_service_code",
    "grant_date",
    "expired_date",
    "cancellation_date",
    "eligibility_rule_num",
    "reserved1",
    "alien",
    "alien_government",
    "alien_corporation",
    "alien_officer",
    "alien_control",
    "revoked",
    "convicted",
    "adjudged",
    "reserved2",
    "common_carrier",
    "non_common_carrier",
    "private_comm",
    "fixed",
    "mobile",
    "radiolocation",
    "satellite",
    "developmental_or_sta",
    "interconnected_service",
    "certifier_first_name",
    "certifier_mi",
    "certifier_last_name",
    "certifier_suffix",
    "certifier_title",
    "female",
    "black_or_african_american",
    "native_american",
    "hawaiian",
    "asian",
    "white",
    "hispanic",
    "effective_date",
    "last_action_date",
]

EN_COLUMNS = [
    "record_type",
    "unique_system_identifier",
    "uls_file_number",
    "ebf_number",
    "call_sign",
    "entity_type",
    "licensee_id",
    "entity_name",
    "first_name",
    "mi",
    "last_name",
    "suffix",
    "phone",
    "fax",
    "email",
    "street_address",
    "city",
    "state",
    "zip_code",
    "po_box",
    "attention_line",
    "sgin",
    "frn",
    "applicant_type_code",
    "applicant_type_other",
    "status_code",
    "status_date",
]

AM_COLUMNS = [
    "record_type",
    "unique_system_identifier",
    "uls_file_number",
    "ebf_number",
    "call_sign",
    "operator_class",
    "group_code",
    "region_code",
    "trustee_call_sign",
    "trustee_indicator",
    "physician_certification",
    "ve_signature",
    "systematic_call_sign_change",
    "vanity_call_sign_change",
    "vanity_relationship",
    "previous_call_sign",
    "previous_operator_class",
    "trustee_name",
]


def _dat_path(name: str) -> Path:
    path = FCC_RAW_DIR / "extract" / name
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run `ham download` (or `ham download-fcc`) first."
        )
    return path


def _columns_sql(columns: list[str]) -> str:
    """DuckDB read_csv columns={...} map: all VARCHAR."""
    return "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in columns) + "}"


def load_uls(*, force: bool = False, as_of: date | None = None) -> Path:
    """
    Load HD + EN + AM into DuckDB and write licenses.parquet.

    Unexpired A records and supported pending renewals. One row per call_sign, preferring
    the highest unique_system_identifier among actives. Licensee entity only
    (entity_type = 'L').
    """
    ensure_dirs()
    as_of = as_of or datetime.now(timezone.utc).date()
    hd_path = _dat_path("HD.dat")
    en_path = _dat_path("EN.dat")
    am_path = _dat_path("AM.dat")
    app_paths = [FCC_RAW_DIR / "applications" / name for name in ("AD.dat", "HD.dat", "EN.dat")]
    for path in app_paths:
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Run `ham download-fcc` first; renewal data is required.")
    metadata_path = PROCESSED_DIR / "license_status.json"
    fingerprint = {
        "version": 1,
        "as_of": as_of.isoformat(),
        "inputs": {str(p): [p.stat().st_size, p.stat().st_mtime_ns]
                   for p in [hd_path, en_path, am_path, *app_paths]},
    }
    cache_outputs = [LICENSES_PARQUET, DUCKDB_PATH, metadata_path,
                     PROCESSED_DIR / "license_classifications.parquet",
                     OUTPUT_TABLES / "license_status_counts.csv",
                     OUTPUT_TABLES / "license_review.csv"]
    if all(p.exists() for p in cache_outputs) and not force:
        cached = json.loads(metadata_path.read_text())
        if cached.get("fingerprint") == fingerprint:
            print(f"  Using cached {LICENSES_PARQUET}")
            return LICENSES_PARQUET

    print("  Loading ULS into DuckDB (this may take a minute)...")
    if DUCKDB_PATH.exists():
        DUCKDB_PATH.unlink()

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        # null_padding: real ULS rows often have more trailing fields than we name.
        # Files are effectively ASCII/UTF-8; DuckDB rejects encoding='latin-1' here.
        common_opts = """
            delim='|',
            header=false,
            auto_detect=false,
            quote='',
            ignore_errors=false,
            nullstr='',
            encoding='utf-8',
            null_padding=true,
            strict_mode=false
        """
        con.execute(
            f"""
            CREATE OR REPLACE TABLE hd_raw AS
            SELECT * FROM read_csv(
                '{_sql_path(hd_path)}',
                {common_opts},
                columns={_columns_sql(HD_COLUMNS)}
            )
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE en_raw AS
            SELECT * FROM read_csv(
                '{_sql_path(en_path)}',
                {common_opts},
                columns={_columns_sql(EN_COLUMNS)}
            )
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE am_raw AS
            SELECT * FROM read_csv(
                '{_sql_path(am_path)}',
                {common_opts},
                columns={_columns_sql(AM_COLUMNS)}
            )
            """
        )

        # Clean and join: active HD, licensee EN, AM operator class
        con.execute(
            """
            CREATE OR REPLACE TABLE licenses AS
            WITH hd AS (
                SELECT
                    TRY_CAST(unique_system_identifier AS BIGINT) AS system_id,
                    TRIM(call_sign) AS call_sign,
                    TRIM(license_status) AS license_status,
                    TRIM(radio_service_code) AS radio_service_code,
                    TRY_STRPTIME(grant_date, '%m/%d/%Y')::DATE AS grant_date,
                    TRY_STRPTIME(expired_date, '%m/%d/%Y')::DATE AS expired_date,
                    TRY_STRPTIME(cancellation_date, '%m/%d/%Y')::DATE AS cancellation_date,
                    TRY_STRPTIME(effective_date, '%m/%d/%Y')::DATE AS effective_date,
                    TRY_STRPTIME(last_action_date, '%m/%d/%Y')::DATE AS last_action_date
                FROM hd_raw
                WHERE TRIM(license_status) = 'A'
                  AND call_sign IS NOT NULL
                  AND TRIM(call_sign) <> ''
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY call_sign
                           ORDER BY system_id DESC NULLS LAST
                       ) AS rn
                FROM hd
            ),
            hd_one AS (
                SELECT * EXCLUDE (rn) FROM ranked WHERE rn = 1
            ),
            en AS (
                SELECT
                    TRY_CAST(unique_system_identifier AS BIGINT) AS system_id,
                    TRIM(entity_type) AS entity_type,
                    TRIM(entity_name) AS entity_name,
                    TRIM(first_name) AS first_name,
                    TRIM(last_name) AS last_name,
                    TRIM(city) AS city,
                    UPPER(TRIM(state)) AS state,
                    SUBSTRING(REGEXP_REPLACE(COALESCE(zip_code, ''), '[^0-9]', '', 'g'), 1, 5) AS zip5,
                    NULLIF(TRIM(frn), '') AS frn,
                    TRIM(applicant_type_code) AS applicant_type_code
                FROM en_raw
                WHERE TRIM(entity_type) = 'L'
            ),
            am AS (
                SELECT
                    TRY_CAST(unique_system_identifier AS BIGINT) AS system_id,
                    UPPER(TRIM(operator_class)) AS operator_class
                FROM am_raw
            )
            SELECT
                h.call_sign,
                h.system_id,
                h.license_status,
                h.radio_service_code,
                h.grant_date,
                h.expired_date,
                h.cancellation_date,
                h.effective_date,
                h.last_action_date,
                a.operator_class,
                e.entity_name,
                e.first_name,
                e.last_name,
                e.city,
                e.state,
                e.zip5,
                e.applicant_type_code,
                e.frn
            FROM hd_one h
            LEFT JOIN en e ON h.system_id = e.system_id
            LEFT JOIN am a ON h.system_id = a.system_id
            """
        )

        from ham_analysis.renewals import classify_licenses

        classify_licenses(con, FCC_RAW_DIR / "applications", as_of,
                          common_opts=common_opts, hd_columns=HD_COLUMNS, en_columns=EN_COLUMNS)
        OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY license_classifications TO '{_sql_path(PROCESSED_DIR / 'license_classifications.parquet')}' (FORMAT PARQUET)")
        con.execute(f"""
            COPY (SELECT state AS fcc_mailing_state, license_category, COUNT(*) AS license_count
                  FROM license_classifications GROUP BY ALL ORDER BY 1, 2)
            TO '{_sql_path(OUTPUT_TABLES / 'license_status_counts.csv')}' (HEADER, DELIMITER ',')
        """)
        con.execute(f"""
            COPY (SELECT call_sign, system_id, state AS fcc_mailing_state, expired_date,
                         renewal_file_number, renewal_received_date, renewal_status, renewal_basis
                  FROM license_classifications WHERE license_category = 'unresolved'
                  ORDER BY state, call_sign)
            TO '{_sql_path(OUTPUT_TABLES / 'license_review.csv')}' (HEADER, DELIMITER ',')
        """)
        counts = dict(con.execute("SELECT license_category, COUNT(*) FROM license_classifications GROUP BY 1").fetchall())
        print(f"  License classification as of {as_of}: {counts}")

        n = con.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]
        n_state = con.execute(
            "SELECT COUNT(*) FROM licenses WHERE state IS NOT NULL AND state <> ''"
        ).fetchone()[0]
        n_zip = con.execute(
            "SELECT COUNT(*) FROM licenses WHERE zip5 IS NOT NULL AND LENGTH(zip5) = 5"
        ).fetchone()[0]
        print(f"  Active licenses: {n:,}")
        print(f"  With state:      {n_state:,}")
        print(f"  With ZIP5:       {n_zip:,}")

        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"COPY licenses TO '{_sql_path(LICENSES_PARQUET)}' (FORMAT PARQUET)"
        )
        metadata_path.write_text(json.dumps({"fingerprint": fingerprint, "as_of": as_of.isoformat(),
                                             "counts": counts}, indent=2) + "\n")
        print(f"  → {LICENSES_PARQUET}")
    finally:
        con.close()

    return LICENSES_PARQUET


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")
