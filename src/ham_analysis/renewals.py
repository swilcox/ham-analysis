"""Classify active ULS records using renewal application evidence.

Sources and limitations are documented in README.md. These categories describe
the evidence in a weekly snapshot; they are not individual operating advice.
"""

from datetime import date
from pathlib import Path

import duckdb


AD_COLUMNS = [
    "record_type", "unique_system_identifier", "uls_file_number", "ebf_number",
    "purpose", "status", "fee_exempt", "regulatory_fee_exempt", "source",
    "requested_expiration", "receipt_date", "notification_code", "notification_date",
    "expanding", "major_minor", "original_purpose", "waiver_requested",
]


def classify_licenses(
    con: duckdb.DuckDBPyConnection, application_dir: Path, as_of: date, *,
    common_opts: str, hd_columns: list[str], en_columns: list[str],
) -> None:
    """Replace licenses with counted rows and retain all A rows for auditing."""
    for table, filename, columns in (
        ("app_ad", "AD.dat", AD_COLUMNS),
        ("app_hd", "HD.dat", hd_columns),
        ("app_en", "EN.dat", en_columns),
    ):
        path = str((application_dir / filename).resolve()).replace("'", "''")
        layout = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in columns) + "}"
        con.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_csv('{path}', {common_opts}, columns={layout})")

    # Rank all versions BEFORE filtering purpose/status. An inactive original or
    # a withdrawn/dismissed amendment must never resurrect an older pending row.
    # Application IDs are not license IDs. HD/AD/EN join on application ID;
    # call sign + FRN then identify the license holder.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE renewal_applications AS
        WITH versions AS (
            SELECT *,
                   MIN(TRY_STRPTIME(receipt_date, '%m/%d/%Y')::DATE) OVER (
                       PARTITION BY uls_file_number) AS original_received_date,
                   ROW_NUMBER() OVER (PARTITION BY uls_file_number
                       ORDER BY TRY_CAST(unique_system_identifier AS BIGINT) DESC) AS rn
            FROM app_ad WHERE NULLIF(TRIM(uls_file_number), '') IS NOT NULL
        )
        SELECT DISTINCT
               TRIM(h.call_sign) AS call_sign,
               NULLIF(TRIM(e.frn), '') AS frn,
               TRIM(a.uls_file_number) AS file_number,
               TRIM(a.status) AS status,
               a.original_received_date AS received_date
        FROM versions a
        JOIN app_hd h ON a.unique_system_identifier = h.unique_system_identifier
        LEFT JOIN app_en e ON a.unique_system_identifier = e.unique_system_identifier
                         AND TRIM(e.entity_type) = 'L'
        WHERE a.rn = 1
          AND (TRIM(a.purpose) IN ('RO', 'RM') OR
               (TRIM(a.purpose) = 'AM' AND TRIM(a.original_purpose) IN ('RO', 'RM')))
          AND COALESCE(TRIM(a.status), '') NOT IN ('D', 'G', 'H', 'I', 'K', 'T', 'W', 'N')
          AND TRIM(h.radio_service_code) IN ('HA', 'HV')
    """)

    con.execute("""
        CREATE OR REPLACE TEMP TABLE renewal_evidence AS
        WITH candidates AS (
            SELECT l.system_id, a.*, l.expired_date,
                   CASE
                       WHEN ?::DATE >= DATE '2025-11-17'
                        AND l.expired_date BETWEEN DATE '2025-10-01' AND DATE '2026-03-05'
                       THEN DATE '2026-03-05'
                       ELSE l.expired_date
                   END AS filing_deadline,
                   l.frn AS license_frn, l.grant_date
            FROM licenses l JOIN renewal_applications a ON l.call_sign = a.call_sign
            WHERE (a.frn = l.frn OR a.frn IS NULL OR l.frn IS NULL)
              AND (a.received_date IS NULL OR a.received_date <= ?)
              AND (a.received_date IS NULL OR l.grant_date IS NULL OR a.received_date >= l.grant_date)
        ), assessed AS (
            SELECT *,
                CASE
                    WHEN frn IS NULL OR license_frn IS NULL THEN 'missing_frn'
                    WHEN received_date IS NULL OR grant_date IS NULL THEN 'missing_date'
                    WHEN status NOT IN ('1', '2', 'R') OR status IS NULL THEN 'unsupported_application_status'
                    WHEN received_date > filing_deadline THEN 'late_or_other_extension'
                    WHEN filing_deadline = DATE '2026-03-05' AND received_date > expired_date THEN 'DA-25-943'
                    ELSE 'timely_renewal'
                END AS basis
            FROM candidates
        )
        SELECT *, basis IN ('timely_renewal', 'DA-25-943') AS qualifies
        FROM assessed
        QUALIFY ROW_NUMBER() OVER (PARTITION BY system_id
            ORDER BY (basis IN ('timely_renewal', 'DA-25-943')) DESC,
                     received_date DESC NULLS LAST, file_number DESC) = 1
    """, [as_of, as_of])

    con.execute("""
        CREATE OR REPLACE TABLE license_classifications AS
        SELECT l.*, ?::DATE AS analysis_date,
               r.file_number AS renewal_file_number,
               r.received_date AS renewal_received_date,
               r.status AS renewal_status,
               CASE WHEN l.expired_date IS NULL THEN 'missing_expiration'
                    WHEN l.cancellation_date <= ? THEN 'cancellation_date'
                    ELSE r.basis END AS renewal_basis,
               CASE
                   WHEN l.cancellation_date <= ? THEN 'unresolved'
                   WHEN l.expired_date IS NULL THEN 'unresolved'
                   WHEN l.expired_date >= ? THEN 'unexpired'
                   WHEN r.qualifies THEN 'continued'
                   WHEN r.file_number IS NOT NULL THEN 'unresolved'
                   WHEN l.expired_date + INTERVAL '2 years' > ? THEN 'grace'
                   ELSE 'expired'
               END AS license_category
        FROM licenses l LEFT JOIN renewal_evidence r ON l.system_id = r.system_id
    """, [as_of] * 5)
    con.execute("""
        CREATE OR REPLACE TABLE licenses AS
        SELECT * FROM license_classifications WHERE license_category IN ('unexpired', 'continued')
    """)
