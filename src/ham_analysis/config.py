"""Paths, URLs, and pipeline defaults."""

from __future__ import annotations

from pathlib import Path

# Repo root: .../ham-analysis (parent of src/)
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"
OUTPUT_TABLES = ROOT / "outputs" / "tables"
OUTPUT_MAPS = ROOT / "outputs" / "maps"
OUTPUT_SITE = ROOT / "outputs" / "site"

FCC_RAW_DIR = RAW_DIR / "fcc"
CENSUS_RAW_DIR = RAW_DIR / "census"

DUCKDB_PATH = PROCESSED_DIR / "ham.duckdb"
LICENSES_PARQUET = PROCESSED_DIR / "licenses.parquet"
GEO_LICENSES_PARQUET = PROCESSED_DIR / "geo_licenses.parquet"
METRICS_STATE_PARQUET = PROCESSED_DIR / "metrics_state.parquet"
METRICS_COUNTY_PARQUET = PROCESSED_DIR / "metrics_county.parquet"
METRICS_STATE_CSV = OUTPUT_TABLES / "metrics_state.csv"
METRICS_COUNTY_CSV = OUTPUT_TABLES / "metrics_county.csv"
COUNTY_AGE_CSV = CENSUS_RAW_DIR / "county_age.csv"
AGE_CORRELATION_CSV = OUTPUT_TABLES / "age_correlation.csv"
AGE_SCATTER_HTML = OUTPUT_MAPS / "county_density_vs_median_age.html"

# FCC ULS — weekly complete amateur license dump
FCC_AMAT_LICENSE_URL = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"
FCC_AMAT_ZIP_NAME = "l_amat.zip"

# Population: Census Population Estimates (co-est) — see download_census.py
# (ACS API now requires a key; PopEst CSVs are key-free.)

# Cartographic Boundary Files (500k = good national choropleth resolution)
CENSUS_CB_YEAR = 2023
COUNTY_BOUNDARY_URL = (
    f"https://www2.census.gov/geo/tiger/GENZ{CENSUS_CB_YEAR}/shp/"
    f"cb_{CENSUS_CB_YEAR}_us_county_500k.zip"
)
STATE_BOUNDARY_URL = (
    f"https://www2.census.gov/geo/tiger/GENZ{CENSUS_CB_YEAR}/shp/"
    f"cb_{CENSUS_CB_YEAR}_us_state_500k.zip"
)

# 2020 ZCTA–County relationship file (for ZIP → county FIPS)
ZCTA_COUNTY_REL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_county20_natl.txt"
)

DEFAULT_GROWTH_MONTHS = 12

# Operator class codes in AM.dat
CLASS_LABELS = {
    "A": "Advanced",
    "E": "Extra",
    "G": "General",
    "N": "Novice",
    "P": "Technician Plus",
    "T": "Technician",
}


def ensure_dirs() -> None:
    """Create data and output directories if missing."""
    for path in (
        FCC_RAW_DIR,
        CENSUS_RAW_DIR,
        PROCESSED_DIR,
        EXTERNAL_DIR,
        OUTPUT_TABLES,
        OUTPUT_MAPS,
        OUTPUT_SITE,
    ):
        path.mkdir(parents=True, exist_ok=True)
