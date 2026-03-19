"""Fetch market intelligence for plumbing merchants from free UK gov sources.

Pulls data from three sources to give sector-level context and
company-level financials for the merchants found by find_plumbing_merchants_v2.py:

  1. ONS UK Business — business counts by SIC code, turnover size band, region
  2. HMRC UKTradeInfo — import/export values for plumbing commodity codes
  3. convert-ixbrl.co.uk — parsed balance sheet / P&L from Companies House filings

Usage:
    # Full run: sector stats + individual company financials
    python fetch_market_data.py -i data/plumbing_merchants_v2.xlsx

    # Sector stats only (no company-level lookups)
    python fetch_market_data.py --sector-only

    # Company financials only (from existing merchant list)
    python fetch_market_data.py -i data/plumbing_merchants_v2.xlsx --company-only

    # If you get SSL errors (corporate proxy / certificate issues):
    python fetch_market_data.py --no-ssl-verify
"""
import argparse
import logging
import os
import time
import urllib3

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Global SSL verification setting (toggled by --no-ssl-verify)
SSL_VERIFY = True

# ---------------------------------------------------------------------------
# Our plumbing SIC codes
# ---------------------------------------------------------------------------
PLUMBING_SIC_CODES = {
    "46740": "Wholesale of hardware, plumbing and heating equipment",
    "43220": "Plumbing, heat and air-conditioning installation",
    "47520": "Retail sale of hardware, paints and glass",
}

# ---------------------------------------------------------------------------
# HMRC commodity codes for plumbing/heating goods (HS chapters/headings)
# Chapter 73: Articles of iron or steel (pipes, fittings, radiators)
# Chapter 74: Copper and articles thereof (copper pipes)
# Chapter 84: Boilers, machinery (central heating boilers)
# ---------------------------------------------------------------------------
PLUMBING_COMMODITY_CODES = {
    "7303": "Tubes, pipes of cast iron",
    "7304": "Tubes, pipes of iron/steel, seamless",
    "7306": "Tubes, pipes of iron/steel, welded",
    "7307": "Tube or pipe fittings of iron/steel",
    "7324": "Sanitary ware of iron/steel",
    "7411": "Copper tubes and pipes",
    "7412": "Copper tube/pipe fittings",
    "8403": "Central heating boilers (not 8402)",
    "8404": "Auxiliary plant for boilers",
    "8415": "Air conditioning machines",
    "8419": "Heat exchange units / water heaters",
}


# =========================================================================
# SOURCE 1: ONS — UK Business Activity, Size and Location
# =========================================================================
ONS_API_BASE = "https://api.beta.ons.gov.uk/v1"
ONS_DATASET_ID = "uk-business-by-enterprises-and-local-units"

# Fallback: direct CSV download from ONS website
ONS_CSV_URL = (
    "https://www.ons.gov.uk/file?uri=/businessindustryandtrade/business/"
    "activitysizeandlocation/datasets/ukbusinessactivitysizeandlocation/"
    "current/ukbaborough.csv"
)


def fetch_ons_business_data():
    """Fetch UK Business counts by SIC code from ONS.

    Tries the beta API first, falls back to direct CSV download.
    Returns a DataFrame with business counts by SIC code.
    """
    logger.info("Fetching ONS UK Business data...")

    # Try the beta API
    try:
        df = _fetch_ons_via_api()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.warning(f"ONS API failed: {e}")

    # Fallback: direct CSV download
    try:
        df = _fetch_ons_via_csv()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.warning(f"ONS CSV download failed: {e}")

    logger.error("Could not fetch ONS data from any source")
    return pd.DataFrame()


def _fetch_ons_via_api():
    """Try fetching from the ONS beta API."""
    url = f"{ONS_API_BASE}/datasets/{ONS_DATASET_ID}/editions"
    resp = requests.get(url, timeout=30, verify=SSL_VERIFY)
    if resp.status_code != 200:
        logger.warning(f"ONS editions endpoint returned {resp.status_code}")
        return None

    editions = resp.json().get("items", [])
    if not editions:
        return None

    latest = editions[0]
    edition_id = latest.get("edition", "")

    versions_url = f"{ONS_API_BASE}/datasets/{ONS_DATASET_ID}/editions/{edition_id}/versions"
    resp = requests.get(versions_url, timeout=30, verify=SSL_VERIFY)
    if resp.status_code != 200:
        return None

    versions = resp.json().get("items", [])
    if not versions:
        return None

    latest_version = versions[0]
    csv_url = latest_version.get("downloads", {}).get("csv", {}).get("href", "")

    if not csv_url:
        return None

    logger.info(f"Downloading ONS data from API: {csv_url}")
    df = pd.read_csv(csv_url, storage_options={"verify": SSL_VERIFY} if not SSL_VERIFY else {})
    return df


def _fetch_ons_via_csv():
    """Direct CSV download fallback."""
    logger.info(f"Trying direct ONS CSV: {ONS_CSV_URL}")
    resp = requests.get(ONS_CSV_URL, timeout=60, verify=SSL_VERIFY)
    resp.raise_for_status()

    import io
    df = pd.read_csv(io.StringIO(resp.text))
    return df


def analyse_ons_for_plumbing(df):
    """Filter and summarise ONS data for our plumbing SIC codes.

    The ONS dataset structure varies by year, so we try multiple column name
    patterns to find SIC code, geography, and count columns.
    """
    if df.empty:
        return {}

    results = {}

    # Try to identify SIC code column
    sic_col = None
    for candidate in ["SIC", "sic", "SIC07", "Industry", "industry", "SIC 2007"]:
        if candidate in df.columns:
            sic_col = candidate
            break

    if sic_col is None:
        # Try partial match
        for col in df.columns:
            if "sic" in col.lower() or "industry" in col.lower():
                sic_col = col
                break

    if sic_col is None:
        logger.warning(f"Could not find SIC column in ONS data. Columns: {list(df.columns)}")
        results["raw_columns"] = list(df.columns)
        results["raw_sample"] = df.head(5).to_dict()
        return results

    logger.info(f"Using SIC column: '{sic_col}'")

    # Filter for our SIC codes
    for sic_code, desc in PLUMBING_SIC_CODES.items():
        mask = df[sic_col].astype(str).str.contains(sic_code, na=False)
        subset = df[mask]

        if subset.empty:
            # Try without leading zeros or with different formatting
            short_code = sic_code.lstrip("0")
            mask = df[sic_col].astype(str).str.contains(short_code, na=False)
            subset = df[mask]

        if not subset.empty:
            results[sic_code] = {
                "description": desc,
                "rows": len(subset),
                "data": subset.to_dict(orient="records"),
            }
            logger.info(f"  SIC {sic_code}: {len(subset)} rows found")
        else:
            logger.info(f"  SIC {sic_code}: no data found")

    return results


# =========================================================================
# SOURCE 2: HMRC UKTradeInfo — Import/Export statistics
# =========================================================================
HMRC_API_BASE = "https://api.uktradeinfo.com"


def fetch_hmrc_trade_data():
    """Fetch import/export data for plumbing-related commodity codes.

    Uses the HMRC UKTradeInfo OTS (Overseas Trade Statistics) API.
    Returns a DataFrame with trade values by commodity code.
    """
    logger.info("Fetching HMRC trade statistics...")
    all_results = []

    for hs_code, desc in PLUMBING_COMMODITY_CODES.items():
        logger.info(f"  Commodity {hs_code}: {desc}")
        try:
            data = _fetch_hmrc_commodity(hs_code)
            if data:
                for row in data:
                    row["commodity_code"] = hs_code
                    row["commodity_description"] = desc
                all_results.extend(data)
        except Exception as e:
            logger.warning(f"  Failed for {hs_code}: {e}")

        time.sleep(0.5)  # Be polite

    if not all_results:
        logger.warning("No HMRC trade data retrieved")
        return pd.DataFrame()

    df = pd.DataFrame(all_results)
    return df


def _fetch_hmrc_commodity(hs_code):
    """Fetch trade data for a specific HS commodity code heading."""
    # OTS endpoint with OData filter
    # Get the most recent year of monthly data
    # Commodity IDs are 8-digit; we search by the 4-digit heading prefix
    code_start = int(hs_code) * 10000  # e.g. 7303 -> 73030000
    code_end = code_start + 9999       # e.g. 73030000 -> 73039999

    url = (
        f"{HMRC_API_BASE}/OTS"
        f"?$filter=CommodityId ge {code_start} and CommodityId le {code_end}"
        f" and MonthId ge 202401"
        f"&$select=MonthId,FlowTypeId,CommodityId,Cn8LongDescription,"
        f"TotalValue,TotalNetMass"
        f"&$top=1000"
        f"&$format=json"
    )

    resp = requests.get(url, timeout=30, headers={"Accept": "application/json"},
                        verify=SSL_VERIFY)

    if resp.status_code == 429:
        logger.warning("HMRC rate limited -- sleeping 60s")
        time.sleep(60)
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"},
                            verify=SSL_VERIFY)

    if resp.status_code != 200:
        logger.warning(f"HMRC API returned {resp.status_code}: {resp.text[:200]}")
        return []

    data = resp.json()
    items = data.get("value", [])
    return items


def summarise_hmrc_data(df):
    """Summarise HMRC trade data into readable stats."""
    if df.empty:
        return {}

    results = {}

    # FlowTypeId: 1 = imports, 2 = exports (typically)
    for code, desc in PLUMBING_COMMODITY_CODES.items():
        mask = df["commodity_code"] == code
        subset = df[mask]
        if subset.empty:
            continue

        total_value = subset["TotalValue"].sum() if "TotalValue" in subset.columns else 0
        total_mass = subset["TotalNetMass"].sum() if "TotalNetMass" in subset.columns else 0

        results[code] = {
            "description": desc,
            "total_value_gbp": total_value,
            "total_net_mass_kg": total_mass,
            "records": len(subset),
        }

    return results


# =========================================================================
# SOURCE 3: convert-ixbrl.co.uk — Company-level financial data
# =========================================================================
IXBRL_API_BASE = "https://convert-ixbrl.co.uk/api"


def fetch_ixbrl_metadata(company_number):
    """Check what financial data is available for a company.

    This is the FREE endpoint -- no auth required.
    Returns a dict of available financial fields or None.
    """
    url = f"{IXBRL_API_BASE}/v2/FinancialsMetaData/{company_number}"
    try:
        resp = requests.get(url, timeout=15, headers={"Accept": "application/json"},
                            verify=SSL_VERIFY)
    except requests.exceptions.SSLError:
        logger.warning("SSL error connecting to convert-ixbrl.co.uk -- "
                       "try running with --no-ssl-verify")
        return None
    except requests.exceptions.ConnectionError:
        return None

    if resp.status_code != 200:
        return None

    try:
        return resp.json()
    except Exception:
        return None


def enrich_merchants_with_financials(df, max_companies=None):
    """For each company in the merchant list, check what financials are available.

    Adds columns indicating which financial data fields exist.
    """
    if df.empty:
        return df

    total = min(len(df), max_companies) if max_companies else len(df)
    logger.info(f"Checking financial data availability for {total} companies...")

    has_turnover = []
    has_balance_sheet = []
    has_pnl = []
    has_employees = []
    financial_fields = []

    for i, (_, row) in enumerate(df.iterrows()):
        if max_companies and i >= max_companies:
            # Fill remaining with empty
            remaining = len(df) - i
            has_turnover.extend([""] * remaining)
            has_balance_sheet.extend([""] * remaining)
            has_pnl.extend([""] * remaining)
            has_employees.extend([""] * remaining)
            financial_fields.extend([""] * remaining)
            break

        cn = str(row.get("company_number", "")).strip()
        if not cn:
            has_turnover.append("")
            has_balance_sheet.append("")
            has_pnl.append("")
            has_employees.append("")
            financial_fields.append("")
            continue

        logger.info(f"  [{i + 1}/{total}] Checking {cn} — {row.get('company_name', '')}")

        meta = fetch_ixbrl_metadata(cn)
        time.sleep(0.3)  # Be polite

        if meta is None:
            has_turnover.append(False)
            has_balance_sheet.append(False)
            has_pnl.append(False)
            has_employees.append(False)
            financial_fields.append("")
            continue

        # Check which fields are available
        available = [k for k, v in meta.items() if v == "available"]
        turnover_available = any("turnover" in f.lower() or "revenue" in f.lower() for f in available)
        bs_available = any("asset" in f.lower() or "liabilit" in f.lower() for f in available)
        pnl_available = any("profit" in f.lower() or "loss" in f.lower() for f in available)
        emp_available = any("employee" in f.lower() or "staff" in f.lower() for f in available)

        has_turnover.append(turnover_available)
        has_balance_sheet.append(bs_available)
        has_pnl.append(pnl_available)
        has_employees.append(emp_available)
        financial_fields.append(", ".join(available) if available else "none")

        if (i + 1) % 50 == 0:
            logger.info(f"  ... checked {i + 1}/{total}")

    df = df.copy()
    df["has_turnover_data"] = has_turnover
    df["has_balance_sheet_data"] = has_balance_sheet
    df["has_pnl_data"] = has_pnl
    df["has_employee_data"] = has_employees
    df["available_financial_fields"] = financial_fields

    return df


# =========================================================================
# Main
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Fetch market intelligence for plumbing merchants"
    )
    parser.add_argument(
        "-i", "--input", default="data/plumbing_merchants_v2.xlsx",
        help="Input merchant list from find_plumbing_merchants_v2.py",
    )
    parser.add_argument(
        "-o", "--output-dir", default="data/market_intelligence",
        help="Output directory for results",
    )
    parser.add_argument(
        "--sector-only", action="store_true",
        help="Only fetch sector-level stats (ONS + HMRC), skip company-level",
    )
    parser.add_argument(
        "--company-only", action="store_true",
        help="Only fetch company-level financials, skip sector stats",
    )
    parser.add_argument(
        "--max-companies", type=int, default=None,
        help="Limit company-level lookups (useful for testing)",
    )
    parser.add_argument(
        "--no-ssl-verify", action="store_true",
        help="Disable SSL certificate verification (for corporate proxies)",
    )
    args = parser.parse_args()

    # Apply SSL setting globally
    global SSL_VERIFY
    if args.no_ssl_verify:
        SSL_VERIFY = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logger.warning("SSL verification disabled (--no-ssl-verify)")

    os.makedirs(args.output_dir, exist_ok=True)

    print("\n" + "=" * 60)
    print("PLUMBING MARKET INTELLIGENCE")
    print("=" * 60)

    # ------------------------------------------------------------------
    # SOURCE 1: ONS UK Business data
    # ------------------------------------------------------------------
    if not args.company_only:
        print("\n--- SOURCE 1: ONS UK Business Data ---")
        ons_df = fetch_ons_business_data()

        if not ons_df.empty:
            # Save raw data
            ons_path = os.path.join(args.output_dir, "ons_uk_business_raw.csv")
            ons_df.to_csv(ons_path, index=False)
            logger.info(f"Saved raw ONS data to {ons_path}")

            # Analyse for plumbing
            ons_results = analyse_ons_for_plumbing(ons_df)

            if ons_results:
                print("\nONS Business Counts for Plumbing SIC Codes:")
                for sic, info in ons_results.items():
                    if sic in ("raw_columns", "raw_sample"):
                        continue
                    print(f"  SIC {sic} ({info['description']}): {info['rows']} data rows")
            else:
                print("  No plumbing-specific data found in ONS dataset")
                if "raw_columns" in ons_results:
                    print(f"  Available columns: {ons_results['raw_columns']}")
        else:
            print("  Could not retrieve ONS data")

    # ------------------------------------------------------------------
    # SOURCE 2: HMRC Trade Statistics
    # ------------------------------------------------------------------
    if not args.company_only:
        print("\n--- SOURCE 2: HMRC Trade Statistics ---")
        hmrc_df = fetch_hmrc_trade_data()

        if not hmrc_df.empty:
            hmrc_path = os.path.join(args.output_dir, "hmrc_plumbing_trade.csv")
            hmrc_df.to_csv(hmrc_path, index=False)
            logger.info(f"Saved HMRC data to {hmrc_path}")

            summary = summarise_hmrc_data(hmrc_df)
            print("\nHMRC Trade Data for Plumbing Commodity Codes:")
            total_value = 0
            for code, info in summary.items():
                val = info["total_value_gbp"]
                total_value += val
                print(f"  HS {code} ({info['description']}): £{val:,.0f}")
            print(f"  TOTAL trade value: £{total_value:,.0f}")
        else:
            print("  Could not retrieve HMRC trade data")

    # ------------------------------------------------------------------
    # SOURCE 3: Company-level financials (convert-ixbrl)
    # ------------------------------------------------------------------
    if not args.sector_only:
        print("\n--- SOURCE 3: Company Financial Data (convert-ixbrl.co.uk) ---")

        if os.path.exists(args.input):
            if args.input.endswith(".xlsx"):
                merchants_df = pd.read_excel(args.input, engine="openpyxl")
            else:
                merchants_df = pd.read_csv(args.input)

            logger.info(f"Loaded {len(merchants_df)} merchants from {args.input}")

            enriched = enrich_merchants_with_financials(
                merchants_df, max_companies=args.max_companies
            )

            # Save enriched data
            enriched_path = os.path.join(args.output_dir, "merchants_with_financials.xlsx")
            enriched.to_excel(enriched_path, index=False, engine="openpyxl")
            logger.info(f"Saved enriched data to {enriched_path}")

            # Summary
            total = len(enriched)
            if "has_turnover_data" in enriched.columns:
                with_turnover = enriched["has_turnover_data"].sum()
                with_bs = enriched["has_balance_sheet_data"].sum()
                with_pnl = enriched["has_pnl_data"].sum()
                with_emp = enriched["has_employee_data"].sum()

                print(f"\nFinancial Data Availability ({total} companies checked):")
                print(f"  Has turnover data:       {with_turnover:>5}  ({with_turnover/total*100:.1f}%)")
                print(f"  Has balance sheet data:  {with_bs:>5}  ({with_bs/total*100:.1f}%)")
                print(f"  Has P&L data:            {with_pnl:>5}  ({with_pnl/total*100:.1f}%)")
                print(f"  Has employee data:       {with_emp:>5}  ({with_emp/total*100:.1f}%)")
        else:
            print(f"  Merchant file not found: {args.input}")
            print(f"  Run find_plumbing_merchants_v2.py first")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("OUTPUT FILES")
    print("=" * 60)
    for f in sorted(os.listdir(args.output_dir)):
        fpath = os.path.join(args.output_dir, f)
        size = os.path.getsize(fpath)
        print(f"  {f:<45} {size:>10,} bytes")
    print("=" * 60)


if __name__ == "__main__":
    main()
