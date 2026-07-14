"""Download Census population, boundaries, and ZCTA–county crosswalk."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from ham_analysis.config import (
    CENSUS_RAW_DIR,
    COUNTY_BOUNDARY_URL,
    EXTERNAL_DIR,
    STATE_BOUNDARY_URL,
    ZCTA_COUNTY_REL_URL,
    ensure_dirs,
)
from ham_analysis.download_fcc import download_file

# Population Estimates (no API key required)
# County file also includes state totals (SUMLEV 040) and US (010).
POPEST_COUNTY_URL = (
    "https://www2.census.gov/programs-surveys/popest/datasets/"
    "2020-2024/counties/totals/co-est2024-alldata.csv"
)
POPEST_YEAR_COL = "POPESTIMATE2024"


def download_acs_population(*, force: bool = False) -> tuple[Path, Path]:
    """
    Fetch latest county/state population estimates (Census PopEst).

    Writes the same CSV shapes the rest of the pipeline expects:
    - acs_population_state.csv: name, population, state_fips
    - acs_population_county.csv: name, population, state_fips, county_fips_3, county_fips
    """
    ensure_dirs()
    state_path = CENSUS_RAW_DIR / "acs_population_state.csv"
    county_path = CENSUS_RAW_DIR / "acs_population_county.csv"
    raw_path = CENSUS_RAW_DIR / "co-est2024-alldata.csv"

    if state_path.exists() and county_path.exists() and not force:
        print(f"  Using cached {state_path}")
        print(f"  Using cached {county_path}")
        return state_path, county_path

    print("  Population estimates (county/state file)")
    download_file(POPEST_COUNTY_URL, raw_path, force=force)

    df = pd.read_csv(
        raw_path,
        dtype={"STATE": str, "COUNTY": str, "SUMLEV": str},
        encoding="latin-1",
    )
    if POPEST_YEAR_COL not in df.columns:
        # Fall back to the newest POPESTIMATE* column present
        pop_cols = [c for c in df.columns if c.startswith("POPESTIMATE")]
        if not pop_cols:
            raise KeyError(f"No POPESTIMATE* columns in {raw_path}")
        pop_col = sorted(pop_cols)[-1]
        print(f"  Using population column {pop_col}")
    else:
        pop_col = POPEST_YEAR_COL

    df["STATE"] = df["STATE"].astype(str).str.zfill(2)
    df["COUNTY"] = df["COUNTY"].astype(str).str.zfill(3)

    # States: SUMLEV 040
    states = df[df["SUMLEV"] == "040"][["STNAME", pop_col, "STATE"]].copy()
    states = states.rename(
        columns={"STNAME": "name", pop_col: "population", "STATE": "state_fips"}
    )
    states["population"] = pd.to_numeric(states["population"], errors="coerce")
    states.to_csv(state_path, index=False)
    print(f"  → {state_path} ({len(states)} rows)")

    # Counties: SUMLEV 050
    counties = df[df["SUMLEV"] == "050"][
        ["CTYNAME", "STNAME", pop_col, "STATE", "COUNTY"]
    ].copy()
    counties["name"] = counties["CTYNAME"] + ", " + counties["STNAME"]
    counties = counties.rename(
        columns={
            pop_col: "population",
            "STATE": "state_fips",
            "COUNTY": "county_fips_3",
        }
    )
    counties["population"] = pd.to_numeric(counties["population"], errors="coerce")
    counties["county_fips"] = counties["state_fips"] + counties["county_fips_3"]
    counties = counties[
        ["name", "population", "state_fips", "county_fips_3", "county_fips"]
    ]
    counties.to_csv(county_path, index=False)
    print(f"  → {county_path} ({len(counties)} rows)")

    return state_path, county_path


def download_boundaries(*, force: bool = False) -> tuple[Path, Path]:
    """Download state and county cartographic boundary shapefile zips."""
    ensure_dirs()
    county_zip = CENSUS_RAW_DIR / "cb_us_county_500k.zip"
    state_zip = CENSUS_RAW_DIR / "cb_us_state_500k.zip"
    print("  County boundaries")
    download_file(COUNTY_BOUNDARY_URL, county_zip, force=force)
    print("  State boundaries")
    download_file(STATE_BOUNDARY_URL, state_zip, force=force)

    county_dir = CENSUS_RAW_DIR / "cb_county"
    state_dir = CENSUS_RAW_DIR / "cb_state"
    _extract_shp_zip(county_zip, county_dir, force=force)
    _extract_shp_zip(state_zip, state_dir, force=force)
    return county_dir, state_dir


def _extract_shp_zip(zip_path: Path, dest_dir: Path, *, force: bool = False) -> None:
    marker = dest_dir / ".extracted"
    if marker.exists() and not force:
        print(f"  Using cached {dest_dir}")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    marker.write_text("ok\n", encoding="utf-8")
    print(f"  Extracted → {dest_dir}")


def download_zcta_county_crosswalk(*, force: bool = False) -> Path:
    """
    Download Census 2020 ZCTA–county relationship file and build a
    primary-county lookup (ZCTA assigned to county with largest land area).
    """
    ensure_dirs()
    raw_path = EXTERNAL_DIR / "tab20_zcta520_county20_natl.txt"
    out_path = EXTERNAL_DIR / "zip_to_county.csv"

    if out_path.exists() and not force:
        print(f"  Using cached {out_path}")
        return out_path

    print("  ZCTA–county relationship file")
    download_file(ZCTA_COUNTY_REL_URL, raw_path, force=force)

    df = pd.read_csv(raw_path, sep="|", dtype=str, low_memory=False)
    cols = {c: c.strip() for c in df.columns}
    df = df.rename(columns=cols)

    zcta_col = _find_col(df, ("GEOID_ZCTA5_20", "GEOID_ZCTA5", "ZCTA5CE20"))
    county_col = _find_col(df, ("GEOID_COUNTY_20", "GEOID_COUNTY", "GEOID"))
    area_col = _find_col(df, ("AREALAND_PART", "AREALAND_ZCTA5_20", "AREALAND"))

    work = df[[zcta_col, county_col, area_col]].copy()
    work.columns = ["zcta", "county_fips", "area_land"]
    work["zcta"] = work["zcta"].astype(str).str.zfill(5).str[:5]
    work["county_fips"] = work["county_fips"].astype(str).str.zfill(5).str[:5]
    work["area_land"] = pd.to_numeric(work["area_land"], errors="coerce").fillna(0)

    work = work.sort_values(["zcta", "area_land"], ascending=[True, False])
    primary = work.drop_duplicates(subset=["zcta"], keep="first")
    multi = work.groupby("zcta").size().rename("county_parts")
    primary = primary.merge(multi, on="zcta", how="left")
    primary["state_fips"] = primary["county_fips"].str[:2]
    primary = primary[["zcta", "county_fips", "state_fips", "county_parts"]]
    primary.to_csv(out_path, index=False)
    print(f"  → {out_path} ({len(primary)} ZCTAs)")
    return out_path


def _find_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    upper_map = {c.upper(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.upper() in upper_map:
            return upper_map[cand.upper()]
    raise KeyError(f"None of {candidates} found in columns: {list(df.columns)}")


def find_shp(directory: Path, pattern: str) -> Path:
    """Locate a .shp file under directory matching a substring."""
    matches = list(directory.rglob(f"*{pattern}*.shp"))
    if not matches:
        raise FileNotFoundError(f"No shapefile matching *{pattern}*.shp under {directory}")
    return matches[0]


# County age/sex estimates (includes median age + 65+ counts; no API key)
POPEST_AGESEX_URL = (
    "https://www2.census.gov/programs-surveys/popest/datasets/"
    "2020-2024/counties/asrh/cc-est2024-agesex-all.csv"
)


def download_county_age(*, force: bool = False) -> Path:
    """
    Download Census county age/sex estimates and write county_age.csv.

    Columns: county_fips, median_age, pct_65plus, age65plus, population_age
    Uses the latest YEAR code in the file (currently 6 ≈ 7/1/2024).
    """
    ensure_dirs()
    from ham_analysis.config import COUNTY_AGE_CSV

    out_path = COUNTY_AGE_CSV
    raw_path = CENSUS_RAW_DIR / "cc-est2024-agesex-all.csv"

    if out_path.exists() and not force:
        print(f"  Using cached {out_path}")
        return out_path

    print("  County age/sex estimates (median age, 65+)")
    download_file(POPEST_AGESEX_URL, raw_path, force=force)

    df = pd.read_csv(
        raw_path,
        encoding="latin-1",
        dtype={"STATE": str, "COUNTY": str, "YEAR": str, "SUMLEV": str},
        usecols=[
            "SUMLEV",
            "STATE",
            "COUNTY",
            "YEAR",
            "POPESTIMATE",
            "AGE65PLUS_TOT",
            "MEDIAN_AGE_TOT",
        ],
    )
    # Counties only; latest YEAR vintage
    df = df[df["SUMLEV"] == "050"].copy()
    latest = sorted(df["YEAR"].unique(), key=lambda y: int(y))[-1]
    df = df[df["YEAR"] == latest].copy()
    print(f"  Using agesex YEAR={latest}")

    df["county_fips"] = df["STATE"].str.zfill(2) + df["COUNTY"].str.zfill(3)
    df["population_age"] = pd.to_numeric(df["POPESTIMATE"], errors="coerce")
    df["age65plus"] = pd.to_numeric(df["AGE65PLUS_TOT"], errors="coerce")
    df["median_age"] = pd.to_numeric(df["MEDIAN_AGE_TOT"], errors="coerce")
    df["pct_65plus"] = (
        100.0 * df["age65plus"] / df["population_age"].replace(0, pd.NA)
    ).round(2)

    out = df[
        ["county_fips", "median_age", "pct_65plus", "age65plus", "population_age"]
    ]
    out.to_csv(out_path, index=False)
    print(f"  → {out_path} ({len(out)} counties)")
    return out_path


def download_census(*, force: bool = False) -> dict[str, Path]:
    """Download all census-side assets used by the pipeline."""
    ensure_dirs()
    print("Census / geography")
    state_pop, county_pop = download_acs_population(force=force)
    county_age = download_county_age(force=force)
    county_dir, state_dir = download_boundaries(force=force)
    zip_crosswalk = download_zcta_county_crosswalk(force=force)
    return {
        "state_population": state_pop,
        "county_population": county_pop,
        "county_age": county_age,
        "county_boundaries": county_dir,
        "state_boundaries": state_dir,
        "zip_to_county": zip_crosswalk,
    }
