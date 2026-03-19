"""Estimate turnover for plumbing merchants discovered by fetch_sic_codes_api.py.

Combines three signals to produce a turnover estimate per company:

  1. Companies House accounts type → size band (micro/small/medium/large)
     Using updated thresholds from 6 April 2025 (Companies Act 2006, 50% uplift).

  2. HMRC VAT annual statistics → median turnover by SIC division & size band
     Downloaded dynamically via the GOV.UK Content API.

  3. ONS TOPSI (Turnover in Production & Services Industries) → sector trend index
     Provides quarter-on-quarter growth for the merchant's SIC division.

The output is the input merchant list enriched with:
  - turnover_band         (e.g. "£0–£1M", "£1M–£15M", "£15M–£54M", "£54M+")
  - size_category          (micro / small / medium / large / unknown)
  - benchmark_median_gbp   (median turnover for that SIC + size from HMRC VAT)
  - sector_trend_index     (latest TOPSI index value for the SIC division)
  - estimated_turnover_gbp (best-effort point estimate)

Usage:
    python estimate_turnover.py
    python estimate_turnover.py -i data/plumbing_merchants_v3.xlsx
    python estimate_turnover.py -i data/plumbing_merchants_v3.xlsx --skip-topsi
    python estimate_turnover.py --download-vat-only
"""
import argparse
import io
import logging
import os
import re
import time

import pandas as pd
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Companies House accounts type → size band mapping
#
# Statutory thresholds (Companies Act 2006, updated 6 April 2025 — 50% uplift):
#   Micro-entity:  turnover ≤ £1M,   balance sheet ≤ £500K,  employees ≤ 10
#   Small:         turnover ≤ £15M,  balance sheet ≤ £7.5M,  employees ≤ 50
#   Medium:        turnover ≤ £54M,  balance sheet ≤ £27M,   employees ≤ 250
#   Large:         exceeds medium thresholds
#
# The `last_accounts_type` field from the CH API tells us what type of accounts
# were filed. This reliably implies company size because:
#   - micro-entity accounts → company meets micro thresholds
#   - small accounts → company meets small thresholds (but not micro)
#   - full accounts from a private company → could be medium or large
#   - group accounts → typically large
# ---------------------------------------------------------------------------
ACCOUNTS_TYPE_MAP = {
    # Type                          → (turnover_band,       size_category)
    "micro-entity":                   ("£0–£1M",            "micro"),
    "dormant":                        ("£0–£1M",            "micro"),
    "total-exemption-small":          ("£0–£15M",           "small"),
    "total-exemption-full":           ("£0–£15M",           "small"),
    "small":                          ("£1M–£15M",          "small"),
    "unaudited-abridged":             ("£1M–£15M",          "small"),
    "audited-abridged":               ("£1M–£15M",          "small"),
    "audit-exemption-subsidiary":     ("£1M–£15M",          "small"),
    "filing-exemption-subsidiary":    ("£1M–£15M",          "small"),
    "medium":                         ("£15M–£54M",         "medium"),
    "partial-exemption":              ("£15M–£54M",         "medium"),
    "initial":                        ("unknown",           "unknown"),
    "interim":                        ("unknown",           "unknown"),
    "full":                           ("£15M+",             "medium_or_large"),
    "group":                          ("£54M+",             "large"),
    "no-accounts-type-available":     ("unknown",           "unknown"),
    "null":                           ("unknown",           "unknown"),
}

# Midpoint estimates per size category (GBP) — used as fallback when no
# HMRC benchmark is available for the specific SIC division.
SIZE_MIDPOINTS = {
    "micro":           500_000,       # £0–£1M
    "small":           5_000_000,     # £1M–£15M
    "medium":          30_000_000,    # £15M–£54M
    "medium_or_large": 40_000_000,    # £15M+ (full accounts, unknown ceiling)
    "large":           100_000_000,   # £54M+
}

# SIC division (2-digit) names for the plumbing-relevant sectors
SIC_DIVISIONS = {
    "25": "Manufacture of fabricated metal products",
    "33": "Repair and installation of machinery",
    "35": "Electricity, gas, steam and air conditioning supply",
    "36": "Water collection, treatment and supply",
    "37": "Sewerage",
    "43": "Specialised construction activities",
    "46": "Wholesale trade (except motor vehicles)",
    "47": "Retail trade (except motor vehicles)",
}

# TOPSI series identifiers (CDID codes) mapped to SIC divisions.
# Source: ONS TOPSI dataset.
TOPSI_SERIES = {
    "46": "JT28",   # Services total (includes wholesale trade)
    "47": "JT28",   # Services total (includes retail)
    "43": "JT28",   # Services total (includes construction services)
    "25": "JT27",   # Manufacturing total
    "33": "JT27",   # Manufacturing total (repair/installation)
}


# =========================================================================
# STEP 1: Map accounts type → size band
# =========================================================================
def map_accounts_to_band(df):
    """Add turnover_band and size_category columns based on last_accounts_type."""
    df = df.copy()

    # The column may be named differently depending on pipeline stage
    acct_col = None
    for candidate in ["last_accounts_type", "accounts_type", "accounts"]:
        if candidate in df.columns:
            acct_col = candidate
            break

    if acct_col is None:
        logger.warning("No accounts type column found — all bands will be 'unknown'")
        df["turnover_band"] = "unknown"
        df["size_category"] = "unknown"
        return df

    bands = []
    categories = []
    for val in df[acct_col].fillna("unknown").astype(str).str.strip().str.lower():
        band, cat = ACCOUNTS_TYPE_MAP.get(val, ("unknown", "unknown"))
        bands.append(band)
        categories.append(cat)

    df["turnover_band"] = bands
    df["size_category"] = categories

    # Summary
    dist = df["size_category"].value_counts()
    logger.info(f"Size distribution:\n{dist.to_string()}")

    return df


# =========================================================================
# STEP 2: HMRC VAT benchmark — download & lookup
# =========================================================================
GOVUK_CONTENT_API = "https://www.gov.uk/api/content/government/statistics/value-added-tax-vat-annual-statistics"


def download_hmrc_vat_ods(output_dir="data"):
    """Download the latest HMRC VAT annual statistics ODS file via GOV.UK Content API."""
    logger.info("Fetching HMRC VAT statistics download URL from GOV.UK Content API...")

    try:
        resp = requests.get(GOVUK_CONTENT_API, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"GOV.UK Content API returned {resp.status_code}")
            return None

        data = resp.json()

        # Find the ODS attachment in the document details
        ods_url = None
        ods_title = None
        details = data.get("details", {})

        # Check documents/attachments in the response
        for doc in details.get("documents", []):
            if isinstance(doc, str) and ".ods" in doc.lower():
                # Extract URL from HTML snippet
                match = re.search(r'href="([^"]*\.ods[^"]*)"', doc)
                if match:
                    ods_url = match.group(1)
                    ods_title = "HMRC VAT Statistics"
                    break

        if not ods_url:
            # Try attachments field
            for attachment in details.get("attachments", []):
                url = attachment.get("url", "")
                if ".ods" in url.lower():
                    ods_url = url
                    ods_title = attachment.get("title", "HMRC VAT Statistics")
                    break

        if not ods_url:
            logger.warning("Could not find ODS download link in GOV.UK Content API response")
            return None

        # Download the ODS file
        logger.info(f"Downloading: {ods_title}")
        logger.info(f"URL: {ods_url}")

        os.makedirs(output_dir, exist_ok=True)
        ods_path = os.path.join(output_dir, "hmrc_vat_annual_statistics.ods")

        resp = requests.get(ods_url, timeout=120)
        if resp.status_code != 200:
            logger.warning(f"ODS download returned {resp.status_code}")
            return None

        with open(ods_path, "wb") as f:
            f.write(resp.content)

        logger.info(f"Saved to {ods_path} ({len(resp.content):,} bytes)")
        return ods_path

    except Exception as e:
        logger.error(f"Failed to download HMRC VAT data: {e}")
        return None


def parse_hmrc_vat_ods(ods_path):
    """Parse the HMRC VAT ODS file and extract turnover by SIC division and size band.

    Returns a DataFrame with columns: sic_division, size_band, trader_count,
    total_turnover_gbp, median_turnover_gbp (estimated from band midpoints).
    """
    if not os.path.exists(ods_path):
        logger.error(f"ODS file not found: {ods_path}")
        return pd.DataFrame()

    try:
        # Read the ODS file — try the Table 3 sheet (turnover by SIC trade group)
        # openpyxl can't read ODS, so we use pandas with odf engine
        sheets = pd.read_excel(ods_path, sheet_name=None, engine="odf")
    except Exception as e:
        logger.error(f"Failed to read ODS file: {e}")
        logger.info("You may need to install odfpy: pip install odfpy")
        return pd.DataFrame()

    logger.info(f"ODS sheets found: {list(sheets.keys())}")

    # Look for the turnover/trader table (usually Table 3 or T3)
    target_sheet = None
    for name in sheets:
        name_lower = name.lower()
        if "t3" in name_lower or "table 3" in name_lower or "turnover" in name_lower:
            target_sheet = name
            break

    if target_sheet is None:
        # Fall back to trying all sheets
        for name, df in sheets.items():
            cols = " ".join(str(c).lower() for c in df.columns)
            vals = " ".join(str(v).lower() for v in df.iloc[:5].values.flatten() if pd.notna(v))
            if "turnover" in cols or "turnover" in vals or "sic" in cols or "sic" in vals:
                target_sheet = name
                break

    if target_sheet is None:
        logger.warning("Could not identify the turnover table in the ODS file")
        logger.info(f"Available sheets: {list(sheets.keys())}")
        # Save a summary of each sheet for debugging
        for name, df in sheets.items():
            logger.info(f"  Sheet '{name}': {df.shape}, cols: {list(df.columns)[:5]}")
        return pd.DataFrame()

    logger.info(f"Using sheet: '{target_sheet}'")
    raw = sheets[target_sheet]

    # The HMRC VAT table is semi-structured — parse it into a usable form.
    # We'll extract rows that have SIC division codes and turnover data.
    records = _parse_vat_table(raw)

    if not records:
        logger.warning("Could not parse any records from the VAT table")
        return pd.DataFrame()

    result = pd.DataFrame(records)
    logger.info(f"Parsed {len(result)} benchmark records from HMRC VAT data")
    return result


def _parse_vat_table(df):
    """Extract SIC division → turnover records from the raw HMRC table.

    The HMRC table structure varies by year but typically has:
    - Rows grouped by SIC 2007 trade classification
    - Columns for turnover bands (£0–£50k, £50k–£100k, ..., £10m+)
    - Values are trader counts per band

    We extract: SIC division, number of traders per turnover band, and compute
    a weighted median turnover estimate.
    """
    records = []

    # Turnover band boundaries (GBP) used in HMRC VAT tables
    TURNOVER_BANDS = [
        (0, 50_000, "£0–£50k"),
        (50_000, 100_000, "£50k–£100k"),
        (100_000, 250_000, "£100k–£250k"),
        (250_000, 500_000, "£250k–£500k"),
        (500_000, 1_000_000, "£500k–£1M"),
        (1_000_000, 2_000_000, "£1M–£2M"),
        (2_000_000, 5_000_000, "£2M–£5M"),
        (5_000_000, 10_000_000, "£5M–£10M"),
        (10_000_000, 50_000_000, "£10M–£50M"),
        (50_000_000, 500_000_000, "£50M+"),
    ]

    # Try to find rows with SIC division codes (2-digit numbers like 46, 43, 47)
    for idx, row in df.iterrows():
        row_vals = [str(v).strip() for v in row.values if pd.notna(v)]
        if not row_vals:
            continue

        # Look for a SIC division code in the first few columns
        sic_div = None
        for val in row_vals[:3]:
            # Match 2-digit SIC division or "Division XX" pattern
            match = re.match(r'^(\d{2})$', val)
            if match and match.group(1) in SIC_DIVISIONS:
                sic_div = match.group(1)
                break
            match = re.match(r'(?:division|group|class)\s*(\d{2})', val, re.IGNORECASE)
            if match and match.group(1) in SIC_DIVISIONS:
                sic_div = match.group(1)
                break

        if sic_div is None:
            continue

        # Try to extract numeric values from the row as trader counts per band
        nums = []
        for val in row_vals:
            try:
                n = float(str(val).replace(",", "").replace(" ", ""))
                if n > 0:
                    nums.append(n)
            except ValueError:
                continue

        if len(nums) >= 3:
            # Estimate total traders and weighted average turnover
            total_traders = sum(nums)
            weighted_sum = 0
            for i, count in enumerate(nums):
                if i < len(TURNOVER_BANDS):
                    lo, hi, _ = TURNOVER_BANDS[i]
                    midpoint = (lo + hi) / 2
                else:
                    midpoint = 75_000_000  # high band fallback
                weighted_sum += count * midpoint

            median_estimate = weighted_sum / total_traders if total_traders > 0 else 0

            records.append({
                "sic_division": sic_div,
                "sic_division_name": SIC_DIVISIONS.get(sic_div, ""),
                "total_traders": int(total_traders),
                "estimated_median_turnover_gbp": round(median_estimate),
            })

    return records


def load_hmrc_benchmarks(data_dir="data"):
    """Load or download HMRC VAT benchmarks. Returns a dict: sic_division → median_turnover."""
    csv_path = os.path.join(data_dir, "hmrc_vat_benchmarks.csv")

    # Use cached CSV if available
    if os.path.exists(csv_path):
        logger.info(f"Loading cached HMRC benchmarks from {csv_path}")
        df = pd.read_csv(csv_path, dtype={"sic_division": str})
        return dict(zip(df["sic_division"], df["estimated_median_turnover_gbp"]))

    # Try downloading ODS
    ods_path = os.path.join(data_dir, "hmrc_vat_annual_statistics.ods")
    if not os.path.exists(ods_path):
        ods_path = download_hmrc_vat_ods(data_dir)

    if ods_path and os.path.exists(ods_path):
        df = parse_hmrc_vat_ods(ods_path)
        if not df.empty:
            df.to_csv(csv_path, index=False)
            logger.info(f"Cached HMRC benchmarks to {csv_path}")
            return dict(zip(df["sic_division"], df["estimated_median_turnover_gbp"]))

    logger.warning("No HMRC VAT benchmarks available — using fallback midpoints only")
    return {}


def apply_hmrc_benchmarks(df, benchmarks):
    """Add benchmark_median_gbp column based on SIC division."""
    df = df.copy()

    sic_col = "sic_codes" if "sic_codes" in df.columns else None
    if sic_col is None:
        df["benchmark_median_gbp"] = None
        return df

    def lookup(sic_str):
        codes = str(sic_str).split(",")
        for code in codes:
            code = code.strip()
            if len(code) >= 2:
                division = code[:2]
                if division in benchmarks:
                    return benchmarks[division]
        return None

    df["benchmark_median_gbp"] = df[sic_col].apply(lookup)
    found = df["benchmark_median_gbp"].notna().sum()
    logger.info(f"HMRC benchmark matched for {found}/{len(df)} companies")
    return df


# =========================================================================
# STEP 3: ONS TOPSI trend index
# =========================================================================
TOPSI_CSV_URL = (
    "https://www.ons.gov.uk/file?uri=/businessindustryandtrade/"
    "manufacturingandproductionindustry/datasets/"
    "turnoverandordersintheproductionandservicesindustriesdataset/"
    "current/topsi.csv"
)


def fetch_topsi_data():
    """Download the ONS TOPSI time series CSV and extract latest values per series."""
    logger.info("Fetching ONS TOPSI data...")

    try:
        resp = requests.get(
            TOPSI_CSV_URL,
            timeout=60,
            headers={"User-Agent": "PlumbingPipeline/1.0 (research)"},
        )
        if resp.status_code != 200:
            logger.warning(f"TOPSI CSV download returned {resp.status_code}")
            return {}

        df = pd.read_csv(io.StringIO(resp.text))
        logger.info(f"TOPSI data: {df.shape[0]} rows, {df.shape[1]} columns")

        return _extract_topsi_latest(df)

    except Exception as e:
        logger.warning(f"Failed to fetch TOPSI data: {e}")
        return {}


def _extract_topsi_latest(df):
    """Extract the latest index value for each CDID series we care about."""
    results = {}

    # TOPSI CSV typically has a 'CDID' or 'cdid' column and time-period columns
    cdid_col = None
    for c in df.columns:
        if c.upper() == "CDID":
            cdid_col = c
            break

    if cdid_col is None:
        # Try: first column might be CDID, with dates as subsequent columns
        # Or it could be a transposed format
        logger.info(f"TOPSI columns: {list(df.columns)[:10]}")

        # Check if any of our target CDIDs appear in the data
        flat = df.values.flatten()
        target_cdids = set(TOPSI_SERIES.values())
        for cdid in target_cdids:
            if cdid in [str(v).strip() for v in flat]:
                logger.info(f"Found CDID {cdid} in data (non-standard layout)")
                break
        else:
            logger.warning("Could not find CDID column in TOPSI data")
            return {}

        # Try to parse as wide-format: rows are series, columns are dates
        df_t = df.set_index(df.columns[0]).T
        for cdid in target_cdids:
            if cdid in df_t.columns:
                series = pd.to_numeric(df_t[cdid], errors="coerce").dropna()
                if not series.empty:
                    results[cdid] = float(series.iloc[-1])
        return results

    # Standard long format
    target_cdids = set(TOPSI_SERIES.values())
    for cdid in target_cdids:
        subset = df[df[cdid_col].astype(str).str.strip() == cdid]
        if subset.empty:
            continue

        # Find the latest numeric value
        # Look for date/period columns
        val_col = None
        for c in ["Value", "value", "v4_0", "V4_0"]:
            if c in subset.columns:
                val_col = c
                break

        if val_col:
            vals = pd.to_numeric(subset[val_col], errors="coerce").dropna()
            if not vals.empty:
                results[cdid] = float(vals.iloc[-1])
        else:
            # Try last numeric column
            for c in reversed(subset.columns):
                vals = pd.to_numeric(subset[c], errors="coerce").dropna()
                if not vals.empty:
                    results[cdid] = float(vals.iloc[-1])
                    break

    logger.info(f"TOPSI latest values: {results}")
    return results


def apply_topsi_trends(df, topsi_data):
    """Add sector_trend_index column based on SIC division → TOPSI CDID mapping."""
    df = df.copy()

    sic_col = "sic_codes" if "sic_codes" in df.columns else None
    if sic_col is None or not topsi_data:
        df["sector_trend_index"] = None
        return df

    def lookup(sic_str):
        codes = str(sic_str).split(",")
        for code in codes:
            code = code.strip()
            if len(code) >= 2:
                division = code[:2]
                cdid = TOPSI_SERIES.get(division)
                if cdid and cdid in topsi_data:
                    return topsi_data[cdid]
        return None

    df["sector_trend_index"] = df[sic_col].apply(lookup)
    found = df["sector_trend_index"].notna().sum()
    logger.info(f"TOPSI trend matched for {found}/{len(df)} companies")
    return df


# =========================================================================
# STEP 4: Combine signals into a turnover estimate
# =========================================================================
def estimate_turnover(df):
    """Produce estimated_turnover_gbp by combining all signals.

    Priority logic:
      1. If HMRC benchmark exists for the SIC division → use it, scaled by size band
      2. Else → use size category midpoint
      3. Apply TOPSI trend adjustment if available (normalise around 100 index)
    """
    df = df.copy()
    estimates = []

    for _, row in df.iterrows():
        size_cat = row.get("size_category", "unknown")
        benchmark = row.get("benchmark_median_gbp")
        trend = row.get("sector_trend_index")

        # Base estimate
        if pd.notna(benchmark) and benchmark > 0:
            base = float(benchmark)
            # Scale by size category relative to the median
            scale = _size_scale_factor(size_cat)
            estimate = base * scale
        elif size_cat in SIZE_MIDPOINTS:
            estimate = SIZE_MIDPOINTS[size_cat]
        else:
            estimate = None

        # Apply trend adjustment (TOPSI index is based around 100)
        if estimate and pd.notna(trend) and trend > 0:
            trend_factor = float(trend) / 100.0
            estimate = estimate * trend_factor

        estimates.append(round(estimate) if estimate else None)

    df["estimated_turnover_gbp"] = estimates

    # Summary stats
    valid = df["estimated_turnover_gbp"].dropna()
    if not valid.empty:
        logger.info(f"Turnover estimates: {len(valid)}/{len(df)} companies")
        logger.info(f"  Median: £{valid.median():,.0f}")
        logger.info(f"  Mean:   £{valid.mean():,.0f}")
        logger.info(f"  Min:    £{valid.min():,.0f}")
        logger.info(f"  Max:    £{valid.max():,.0f}")

    return df


def _size_scale_factor(size_cat):
    """Return a multiplier to scale the sector-wide benchmark by company size.

    The HMRC benchmark is an average across all company sizes in a SIC division.
    We adjust up or down based on the company's filing category.
    """
    return {
        "micro":           0.15,   # much smaller than average
        "small":           0.60,   # below average
        "medium":          1.50,   # above average
        "medium_or_large": 2.00,   # well above average
        "large":           4.00,   # much larger than average
    }.get(size_cat, 1.0)


# =========================================================================
# Main
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Estimate turnover for plumbing merchants"
    )
    parser.add_argument(
        "-i", "--input", default="data/plumbing_merchants_v3.xlsx",
        help="Input merchant list (output of fetch_sic_codes_api.py)",
    )
    parser.add_argument(
        "-o", "--output", default="data/merchants_with_turnover.xlsx",
        help="Output file path",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Also save as CSV",
    )
    parser.add_argument(
        "--skip-topsi", action="store_true",
        help="Skip ONS TOPSI trend data (faster, works offline)",
    )
    parser.add_argument(
        "--download-vat-only", action="store_true",
        help="Only download HMRC VAT data, don't process merchants",
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Directory for cached data files",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("TURNOVER ESTIMATION PIPELINE")
    print("=" * 60)

    # --- Load HMRC VAT benchmarks ---
    print("\n--- STEP 1: HMRC VAT Benchmarks ---")
    benchmarks = load_hmrc_benchmarks(args.data_dir)
    if benchmarks:
        print(f"  Loaded benchmarks for {len(benchmarks)} SIC divisions:")
        for div, median in sorted(benchmarks.items()):
            name = SIC_DIVISIONS.get(div, "")
            print(f"    Division {div} ({name}): £{median:,.0f}")
    else:
        print("  No HMRC benchmarks available — using size-band midpoints")

    if args.download_vat_only:
        print("\n--download-vat-only: done.")
        return

    # --- Load merchant list ---
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        print(f"\nRun fetch_sic_codes_api.py first to generate the merchant list.")
        return

    print(f"\n--- Loading merchants from {args.input} ---")
    if args.input.endswith(".csv"):
        df = pd.read_csv(args.input)
    else:
        df = pd.read_excel(args.input, engine="openpyxl")
    print(f"  Loaded {len(df)} merchants")

    # --- STEP 1: Accounts type → size band ---
    print("\n--- STEP 2: Map Accounts Type → Size Band ---")
    df = map_accounts_to_band(df)

    # --- STEP 2: HMRC benchmarks ---
    print("\n--- STEP 3: Apply HMRC VAT Benchmarks ---")
    df = apply_hmrc_benchmarks(df, benchmarks)

    # --- STEP 3: ONS TOPSI trends ---
    topsi_data = {}
    if not args.skip_topsi:
        print("\n--- STEP 4: ONS TOPSI Sector Trends ---")
        topsi_data = fetch_topsi_data()
        if topsi_data:
            for cdid, val in topsi_data.items():
                print(f"  {cdid}: {val}")
        else:
            print("  No TOPSI data available (will skip trend adjustment)")

    df = apply_topsi_trends(df, topsi_data)

    # --- STEP 4: Combine into estimate ---
    print("\n--- STEP 5: Estimate Turnover ---")
    df = estimate_turnover(df)

    # --- Save ---
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    df.to_excel(args.output, index=False, engine="openpyxl")
    logger.info(f"Saved to {args.output}")

    if args.csv:
        csv_path = args.output.replace(".xlsx", ".csv")
        df.to_csv(csv_path, index=False)
        logger.info(f"Also saved to {csv_path}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("TURNOVER ESTIMATION SUMMARY")
    print("=" * 60)
    print(f"Total merchants:          {len(df)}")

    if "size_category" in df.columns:
        print("\nSize distribution:")
        for cat, count in df["size_category"].value_counts().items():
            pct = count / len(df) * 100
            print(f"  {cat:<20} {count:>6}  ({pct:.1f}%)")

    if "turnover_band" in df.columns:
        print("\nTurnover band distribution:")
        for band, count in df["turnover_band"].value_counts().items():
            pct = count / len(df) * 100
            print(f"  {band:<20} {count:>6}  ({pct:.1f}%)")

    valid = df["estimated_turnover_gbp"].dropna()
    if not valid.empty:
        print(f"\nEstimated turnover stats ({len(valid)} companies):")
        print(f"  Median:  £{valid.median():>14,.0f}")
        print(f"  Mean:    £{valid.mean():>14,.0f}")
        print(f"  Min:     £{valid.min():>14,.0f}")
        print(f"  Max:     £{valid.max():>14,.0f}")
        print(f"  Total:   £{valid.sum():>14,.0f}")

        # Breakdown by size
        print("\nMedian turnover by size category:")
        for cat in ["micro", "small", "medium", "medium_or_large", "large"]:
            subset = df.loc[df["size_category"] == cat, "estimated_turnover_gbp"].dropna()
            if not subset.empty:
                print(f"  {cat:<20} £{subset.median():>12,.0f}  (n={len(subset)})")
    else:
        print("\n  No turnover estimates produced — check input data has accounts type info")

    print(f"\nOutput: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
