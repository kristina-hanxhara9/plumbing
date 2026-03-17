"""
Fetch ALL active companies from Companies House that match
the SIC codes learned from the plumbing merchants pipeline.

Usage:
    # Use SIC codes from the trained model:
    python fetch_all_plumbing.py -m model.pkl -o data/all_plumbing.csv

    # Or specify SIC codes manually:
    python fetch_all_plumbing.py --sic-codes 46740 43220 47523 -o data/all_plumbing.csv
"""

import argparse
import logging
import os
import time

import joblib
import pandas as pd
import requests
from dotenv import load_dotenv

from config import (
    API_BASE_URL,
    ADVANCED_SEARCH_ENDPOINT,
    REQUEST_DELAY,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Advanced search returns max 5000 results per SIC code query.
# Each page can have up to 500 items.
PAGE_SIZE = 500
MAX_RESULTS = 5000


def load_api_key():
    load_dotenv()
    key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not key or key == "your_api_key_here":
        raise ValueError(
            "Set COMPANIES_HOUSE_API_KEY in .env file with a valid API key"
        )
    return key


def get_sic_codes_from_model(model_path):
    """Load trained model and extract the SIC codes it learned."""
    bundle = joblib.load(model_path)
    sic_codes = list(bundle["binarizer"].classes_)
    logger.info(f"Loaded {len(sic_codes)} SIC codes from model: {sic_codes}")
    return sic_codes


def fetch_companies_by_sic(sic_code, api_key, company_status="active"):
    """Fetch all active companies for a single SIC code using Advanced Search.

    Returns a list of company dicts.
    """
    url = f"{API_BASE_URL}{ADVANCED_SEARCH_ENDPOINT}"
    all_companies = []
    start_index = 0

    while start_index < MAX_RESULTS:
        params = {
            "sic_codes": sic_code,
            "company_status": company_status,
            "size": PAGE_SIZE,
            "start_index": start_index,
        }

        resp = requests.get(url, params=params, auth=(api_key, ""))

        if resp.status_code == 429:
            logger.warning("Rate limited — sleeping 60s")
            time.sleep(60)
            resp = requests.get(url, params=params, auth=(api_key, ""))

        if resp.status_code != 200:
            logger.error(
                f"API error {resp.status_code} for SIC {sic_code} "
                f"(start_index={start_index}): {resp.text[:200]}"
            )
            break

        data = resp.json()
        items = data.get("items", [])
        total_hits = data.get("hits", 0)

        if not items:
            break

        for item in items:
            address = item.get("registered_office_address", {}) or {}
            all_companies.append({
                "company_name": item.get("company_name", ""),
                "company_number": item.get("company_number", ""),
                "company_status": item.get("company_status", ""),
                "company_type": item.get("company_type", ""),
                "date_of_creation": item.get("date_of_creation", ""),
                "sic_codes": ",".join(item.get("sic_codes", [])),
                "address_line_1": address.get("address_line_1", ""),
                "address_line_2": address.get("address_line_2", ""),
                "locality": address.get("locality", ""),
                "region": address.get("region", ""),
                "postal_code": address.get("postal_code", ""),
                "country": address.get("country", ""),
                "search_sic_code": sic_code,
            })

        logger.info(
            f"  SIC {sic_code}: fetched {len(all_companies)} / {total_hits} total hits"
        )

        start_index += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

        # Stop if we've fetched all available results
        if start_index >= total_hits:
            break

    return all_companies


def _normalise_name(name):
    """Normalise company name for chain matching.

    Strips common suffixes (LTD, LIMITED, PLC, etc.), lowercases,
    and collapses whitespace so that 'Acme Plumbing Ltd' and
    'ACME PLUMBING LIMITED' are treated as the same chain.
    """
    import re
    name = name.strip().upper()
    # Remove common company suffixes
    name = re.sub(
        r"\b(LIMITED|LTD|PLC|LLP|INC|INCORPORATED|CORP|CORPORATION)\b\.?",
        "", name
    )
    # Remove punctuation and collapse whitespace
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def detect_chains(df):
    """Detect chain businesses: same normalised name, different postcodes.

    Adds columns:
        is_chain (bool): True if the company name appears at multiple postcodes.
        chain_name (str): The normalised name used for grouping.
        chain_branch_count (int): How many branches this chain has.
    """
    df = df.copy()
    df["chain_name"] = df["company_name"].fillna("").apply(_normalise_name)

    # Group by normalised name, count unique postcodes
    chain_stats = (
        df.groupby("chain_name")["postal_code"]
        .nunique()
        .reset_index()
        .rename(columns={"postal_code": "chain_branch_count"})
    )

    df = df.merge(chain_stats, on="chain_name", how="left")
    df["is_chain"] = df["chain_branch_count"] > 1

    chain_count = df.loc[df["is_chain"], "chain_name"].nunique()
    chain_branches = df["is_chain"].sum()
    logger.info(
        f"Chain detection: {chain_count} chains found "
        f"({chain_branches} branches total)"
    )

    return df


def fetch_all_plumbing_companies(sic_codes, api_key):
    """Fetch active companies for all given SIC codes."""
    all_results = []

    for i, sic_code in enumerate(sic_codes):
        logger.info(f"[{i + 1}/{len(sic_codes)}] Fetching SIC code: {sic_code}")
        companies = fetch_companies_by_sic(sic_code, api_key)
        all_results.extend(companies)
        logger.info(f"  Got {len(companies)} companies for SIC {sic_code}")

    logger.info(f"Total results (with possible duplicates): {len(all_results)}")

    # Deduplicate by company_number (a company can match multiple SIC codes)
    df = pd.DataFrame(all_results)
    if df.empty:
        logger.warning("No companies found!")
        return df

    before = len(df)
    df = df.drop_duplicates(subset="company_number", keep="first")
    after = len(df)
    if before != after:
        logger.info(f"Removed {before - after} duplicates — {after} unique companies")

    # Chain detection: same normalised name but different postcodes = chain
    df = detect_chains(df)

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Fetch all active plumbing-related companies from Companies House"
    )
    parser.add_argument(
        "-m", "--model-path", default="model.pkl",
        help="Path to trained model (to extract SIC codes)"
    )
    parser.add_argument(
        "--sic-codes", nargs="+",
        help="Manually specify SIC codes instead of loading from model"
    )
    parser.add_argument(
        "-o", "--output", default="data/all_plumbing.csv",
        help="Path to output CSV file"
    )
    args = parser.parse_args()

    api_key = load_api_key()

    # Get SIC codes: from args or from model
    if args.sic_codes:
        sic_codes = args.sic_codes
        logger.info(f"Using manually specified SIC codes: {sic_codes}")
    else:
        sic_codes = get_sic_codes_from_model(args.model_path)

    # Fetch all matching companies
    df = fetch_all_plumbing_companies(sic_codes, api_key)

    if df.empty:
        logger.error("No companies found. Check your API key and SIC codes.")
        return

    # Save
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    df.to_csv(args.output, index=False)
    logger.info(f"Saved {len(df)} companies to {args.output}")

    # Summary
    print("\n" + "=" * 60)
    print("PLUMBING COMPANIES SEARCH SUMMARY")
    print("=" * 60)
    print(f"SIC codes searched:       {sic_codes}")
    print(f"Unique companies found:   {len(df)}")
    for sic in sic_codes:
        count = df["sic_codes"].str.contains(sic, na=False).sum()
        print(f"  SIC {sic}:               {count} companies")
    if "is_chain" in df.columns:
        chain_names = df.loc[df["is_chain"], "chain_name"].nunique()
        chain_branches = df["is_chain"].sum()
        independent = (~df["is_chain"]).sum()
        print(f"Chain businesses:         {chain_names} chains ({chain_branches} branches)")
        print(f"Independent businesses:   {independent}")
        # Show top chains
        if chain_names > 0:
            top = (
                df.loc[df["is_chain"]]
                .groupby("chain_name")["chain_branch_count"]
                .first()
                .sort_values(ascending=False)
                .head(10)
            )
            print(f"Top chains:")
            for name, count in top.items():
                print(f"  {name}: {count} branches")
    print(f"Output file:              {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
