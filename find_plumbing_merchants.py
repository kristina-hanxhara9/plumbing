"""Find plumbing merchants from Companies House using the optimal filters.

Strategy (based on our analysis of ~1000 known plumbing merchants):
  1. Search by top 3 SIC codes  → catches ~32% of plumbing merchants
  2. Search by top 3 name keywords → catches most of the remaining ~53%
  3. Only active companies
  4. Full address and company details

Usage:
    python find_plumbing_merchants.py
    python find_plumbing_merchants.py -o data/plumbing_merchants.xlsx
"""
import argparse
import logging
import os
import re
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from config import API_BASE_URL, ADVANCED_SEARCH_ENDPOINT, COMPANY_ENDPOINT, REQUEST_DELAY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 500
MAX_RESULTS = 5000

# Optimal filters from our analysis
TOP_3_SIC_CODES = ["46740", "43220", "47520"]
TOP_3_KEYWORDS = ["plumb", "heating", "supplies"]


def load_api_key():
    load_dotenv()
    key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not key or key == "your_api_key_here":
        raise ValueError("Set COMPANIES_HOUSE_API_KEY in .env file")
    return key


def _api_get(url, params, api_key, retries=3):
    """Make an API GET request with rate-limit handling."""
    for attempt in range(retries):
        resp = requests.get(url, params=params, auth=(api_key, ""))
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            logger.warning(f"Rate limited — sleeping {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


def fetch_by_sic(sic_code, api_key):
    """Fetch all active companies for a SIC code via Advanced Search."""
    url = f"{API_BASE_URL}{ADVANCED_SEARCH_ENDPOINT}"
    companies = []
    start_index = 0

    while start_index < MAX_RESULTS:
        params = {
            "sic_codes": sic_code,
            "company_status": "active",
            "size": PAGE_SIZE,
            "start_index": start_index,
        }
        resp = _api_get(url, params, api_key)
        if resp.status_code != 200:
            logger.error(f"API error {resp.status_code} for SIC {sic_code}: {resp.text[:200]}")
            break

        data = resp.json()
        items = data.get("items", [])
        total_hits = data.get("hits", 0)

        if not items:
            break

        for item in items:
            companies.append(_extract_company(item, source=f"SIC:{sic_code}"))

        logger.info(f"  SIC {sic_code}: fetched {len(companies)} / {total_hits}")

        start_index += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

        if start_index >= total_hits:
            break

    return companies


def fetch_by_keyword(keyword, api_key):
    """Fetch all active companies matching a name keyword via Advanced Search."""
    url = f"{API_BASE_URL}{ADVANCED_SEARCH_ENDPOINT}"
    companies = []
    start_index = 0

    while start_index < MAX_RESULTS:
        params = {
            "company_name_includes": keyword,
            "company_status": "active",
            "size": PAGE_SIZE,
            "start_index": start_index,
        }
        resp = _api_get(url, params, api_key)
        if resp.status_code != 200:
            logger.error(f"API error {resp.status_code} for keyword '{keyword}': {resp.text[:200]}")
            break

        data = resp.json()
        items = data.get("items", [])
        total_hits = data.get("hits", 0)

        if not items:
            break

        for item in items:
            companies.append(_extract_company(item, source=f"keyword:{keyword}"))

        logger.info(f"  Keyword '{keyword}': fetched {len(companies)} / {total_hits}")

        start_index += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

        if start_index >= total_hits:
            break

    return companies


def _extract_company(item, source=""):
    """Extract company details from an API response item."""
    address = item.get("registered_office_address", {}) or {}
    sic_codes = item.get("sic_codes", [])

    return {
        "company_name": item.get("company_name", ""),
        "company_number": item.get("company_number", ""),
        "company_status": item.get("company_status", ""),
        "company_type": item.get("company_type", ""),
        "date_of_creation": item.get("date_of_creation", ""),
        "sic_codes": ",".join(sic_codes) if isinstance(sic_codes, list) else str(sic_codes),
        "address_line_1": address.get("address_line_1", ""),
        "address_line_2": address.get("address_line_2", ""),
        "locality": address.get("locality", ""),
        "region": address.get("region", ""),
        "postal_code": address.get("postal_code", ""),
        "country": address.get("country", ""),
        "source": source,
    }


def _normalise_name(name):
    """Normalise company name for chain detection."""
    name = name.strip().upper()
    name = re.sub(
        r"\b(LIMITED|LTD|PLC|LLP|INC|INCORPORATED|CORP|CORPORATION)\b\.?",
        "", name,
    )
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def detect_chains(df):
    """Flag chain businesses (same normalised name, different postcodes)."""
    df = df.copy()
    df["chain_name"] = df["company_name"].fillna("").apply(_normalise_name)

    chain_stats = (
        df.groupby("chain_name")["postal_code"]
        .nunique()
        .reset_index()
        .rename(columns={"postal_code": "chain_branch_count"})
    )

    df = df.merge(chain_stats, on="chain_name", how="left")
    df["is_chain"] = df["chain_branch_count"] > 1
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Find plumbing merchants from Companies House API"
    )
    parser.add_argument(
        "-o", "--output", default="data/plumbing_merchants.xlsx",
        help="Output Excel file (default: data/plumbing_merchants.xlsx)",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Also save as CSV",
    )
    args = parser.parse_args()

    api_key = load_api_key()
    all_companies = []

    # --- Strategy 1: Search by SIC codes ---
    print("\n" + "=" * 60)
    print("STEP 1: Fetching by SIC codes (top 3 plumbing codes)")
    print("=" * 60)
    for sic in TOP_3_SIC_CODES:
        logger.info(f"Fetching SIC {sic}...")
        results = fetch_by_sic(sic, api_key)
        all_companies.extend(results)
        logger.info(f"  → {len(results)} active companies")

    sic_count = len(all_companies)
    logger.info(f"Total from SIC search: {sic_count}")

    # --- Strategy 2: Search by name keywords ---
    print("\n" + "=" * 60)
    print("STEP 2: Fetching by name keywords (top 3 plumbing keywords)")
    print("=" * 60)
    for kw in TOP_3_KEYWORDS:
        logger.info(f"Fetching keyword '{kw}'...")
        results = fetch_by_keyword(kw, api_key)
        all_companies.extend(results)
        logger.info(f"  → {len(results)} active companies")

    total_raw = len(all_companies)
    logger.info(f"Total raw results (SIC + keywords): {total_raw}")

    # --- Combine and deduplicate ---
    df = pd.DataFrame(all_companies)
    if df.empty:
        logger.error("No companies found. Check your API key.")
        return

    before = len(df)
    df = df.drop_duplicates(subset="company_number", keep="first")
    dupes = before - len(df)
    logger.info(f"Removed {dupes} duplicates — {len(df)} unique companies")

    # --- Chain detection ---
    df = detect_chains(df)

    # --- Classify how each company was found ---
    has_plumbing_sic = df["sic_codes"].apply(
        lambda x: bool(set(str(x).split(",")) & set(TOP_3_SIC_CODES))
    )
    has_keyword = df["company_name"].apply(
        lambda n: any(kw in str(n).lower() for kw in TOP_3_KEYWORDS)
    )
    df["match_type"] = "other"
    df.loc[has_plumbing_sic & has_keyword, "match_type"] = "SIC + keyword"
    df.loc[has_plumbing_sic & ~has_keyword, "match_type"] = "SIC only"
    df.loc[~has_plumbing_sic & has_keyword, "match_type"] = "keyword only"

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
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"SIC codes searched:       {TOP_3_SIC_CODES}")
    print(f"Keywords searched:        {TOP_3_KEYWORDS}")
    print(f"Total raw results:        {total_raw}")
    print(f"Duplicates removed:       {dupes}")
    print(f"Unique active companies:  {len(df)}")
    print()
    print("Match type breakdown:")
    for mtype, count in df["match_type"].value_counts().items():
        print(f"  {mtype:<20} {count:>6}  ({count/len(df)*100:.1f}%)")
    print()
    for sic in TOP_3_SIC_CODES:
        count = df["sic_codes"].str.contains(sic, na=False).sum()
        print(f"  SIC {sic}:             {count}")
    print()
    if "is_chain" in df.columns:
        chains = df.loc[df["is_chain"], "chain_name"].nunique()
        branches = df["is_chain"].sum()
        independent = (~df["is_chain"]).sum()
        print(f"Chain businesses:         {chains} chains ({branches} branches)")
        print(f"Independent businesses:   {independent}")
        if chains > 0:
            top = (
                df.loc[df["is_chain"]]
                .groupby("chain_name")["chain_branch_count"]
                .first()
                .sort_values(ascending=False)
                .head(10)
            )
            print("Top chains:")
            for name, count in top.items():
                print(f"  {name}: {count} branches")
    print(f"\nOutput: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
