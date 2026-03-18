"""Find plumbing merchants from Companies House — full details + confidence (v2).

Focused search — excludes generic terms (wholesale, merchant, supplies)
that pull in too many non-plumbing companies.

Strategy:
  1. Search by top 3 SIC codes (46740, 43220, 47520)
  2. Search by focused name keywords (plumb, heating)
  3. Only active companies
  4. Fetch FULL company profile for every match (all API fields)
  5. Add SIC descriptions from reference data
  6. Score each company with a confidence % of being a plumbing merchant

Usage:
    python find_plumbing_merchants_v2.py
    python find_plumbing_merchants_v2.py -o data/plumbing_merchants_v2.xlsx
    python find_plumbing_merchants_v2.py --skip-profiles
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

# ---------------------------------------------------------------------------
# Optimal filters from our analysis of ~1000 known plumbing merchants
# ---------------------------------------------------------------------------
TOP_SIC_CODES = ["46740", "43220", "47520"]
TOP_KEYWORDS = ["plumb", "heating"]

# Weights for confidence scoring (sum to ~1.0 at maximum)
CONFIDENCE_WEIGHTS = {
    "has_top3_sic": 0.40,       # Has one of the top 3 SIC codes
    "name_plumb": 0.35,         # Name contains "plumb"
    "name_heating": 0.15,       # Name contains "heating"
    "name_bathroom": 0.10,      # Name contains "bathroom"
}


def load_api_key():
    load_dotenv()
    key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not key or key == "your_api_key_here":
        raise ValueError("Set COMPANIES_HOUSE_API_KEY in .env file")
    return key


def load_sic_descriptions():
    """Load SIC code → description mapping from reference CSV."""
    ref_path = os.path.join(os.path.dirname(__file__), "data", "sic_reference.csv")
    if not os.path.exists(ref_path):
        logger.warning(f"SIC reference file not found at {ref_path}")
        return {}
    df = pd.read_csv(ref_path, dtype={"sic_code": str})
    return dict(zip(df["sic_code"], df["sic_description"]))


def _api_get(url, params, api_key, retries=3):
    """GET request with rate-limit handling."""
    for attempt in range(retries):
        resp = requests.get(url, params=params, auth=(api_key, ""))
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            logger.warning(f"Rate limited — sleeping {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


# ---------------------------------------------------------------------------
# Advanced Search (bulk discovery)
# ---------------------------------------------------------------------------
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
            companies.append(_extract_advanced(item, source=f"SIC:{sic_code}"))

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
            companies.append(_extract_advanced(item, source=f"keyword:{keyword}"))

        logger.info(f"  Keyword '{keyword}': fetched {len(companies)} / {total_hits}")
        start_index += PAGE_SIZE
        time.sleep(REQUEST_DELAY)
        if start_index >= total_hits:
            break

    return companies


def _extract_advanced(item, source=""):
    """Extract all fields from an Advanced Search result."""
    address = item.get("registered_office_address", {}) or {}
    sic_codes = item.get("sic_codes", [])

    return {
        "company_name": item.get("company_name", ""),
        "company_number": item.get("company_number", ""),
        "company_status": item.get("company_status", ""),
        "company_type": item.get("company_type", ""),
        "company_subtype": item.get("company_subtype", ""),
        "kind": item.get("kind", ""),
        "date_of_creation": item.get("date_of_creation", ""),
        "date_of_cessation": item.get("date_of_cessation", ""),
        "sic_codes": ",".join(sic_codes) if isinstance(sic_codes, list) else str(sic_codes),
        "address_line_1": address.get("address_line_1", ""),
        "address_line_2": address.get("address_line_2", ""),
        "locality": address.get("locality", ""),
        "region": address.get("region", ""),
        "postal_code": address.get("postal_code", ""),
        "country": address.get("country", ""),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Full Company Profile (all API fields)
# ---------------------------------------------------------------------------
def fetch_full_profile(company_number, api_key):
    """Fetch the complete company profile — every field the API returns."""
    url = f"{API_BASE_URL}{COMPANY_ENDPOINT.format(company_number=company_number)}"
    resp = _api_get(url, {}, api_key)

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        logger.warning(f"Profile error {resp.status_code} for {company_number}")
        return None

    data = resp.json()
    address = data.get("registered_office_address", {}) or {}
    accounts = data.get("accounts", {}) or {}
    conf_stmt = data.get("confirmation_statement", {}) or {}
    last_accounts = accounts.get("last_accounts", {}) or {}
    next_accounts = accounts.get("next_accounts", {}) or {}
    sic_codes = data.get("sic_codes", [])
    prev_names = data.get("previous_company_names", [])

    return {
        # Core identity
        "company_name": data.get("company_name", ""),
        "company_number": data.get("company_number", ""),
        "company_status": data.get("company_status", ""),
        "company_status_detail": data.get("company_status_detail", ""),
        "company_type": data.get("company_type", ""),
        "jurisdiction": data.get("jurisdiction", ""),
        "date_of_creation": data.get("date_of_creation", ""),
        "date_of_cessation": data.get("date_of_cessation", ""),
        "can_file": data.get("can_file", ""),
        "has_been_liquidated": data.get("has_been_liquidated", ""),
        "has_charges": data.get("has_charges", ""),
        "has_insolvency_history": data.get("has_insolvency_history", ""),
        "is_community_interest_company": data.get("is_community_interest_company", ""),
        "registered_office_is_in_dispute": data.get("registered_office_is_in_dispute", ""),
        "undeliverable_registered_office_address": data.get(
            "undeliverable_registered_office_address", ""
        ),
        "etag": data.get("etag", ""),

        # SIC codes
        "sic_codes": ",".join(sic_codes) if isinstance(sic_codes, list) else str(sic_codes),

        # Registered office address — every field
        "address_care_of": address.get("care_of", ""),
        "address_premises": address.get("premises", ""),
        "address_po_box": address.get("po_box", ""),
        "address_line_1": address.get("address_line_1", ""),
        "address_line_2": address.get("address_line_2", ""),
        "locality": address.get("locality", ""),
        "region": address.get("region", ""),
        "postal_code": address.get("postal_code", ""),
        "country": address.get("country", ""),

        # Accounts
        "last_accounts_made_up_to": last_accounts.get("made_up_to", ""),
        "last_accounts_type": last_accounts.get("type", ""),
        "next_accounts_due": next_accounts.get("due_on", ""),
        "next_accounts_period_end": next_accounts.get("period_end_on", ""),
        "accounts_overdue": accounts.get("overdue", ""),

        # Confirmation statement
        "conf_stmt_last_made_up_to": conf_stmt.get("last_made_up_to", ""),
        "conf_stmt_next_due": conf_stmt.get("next_due", ""),
        "conf_stmt_next_made_up_to": conf_stmt.get("next_made_up_to", ""),
        "conf_stmt_overdue": conf_stmt.get("overdue", ""),

        # Previous names
        "previous_names": "; ".join(
            f"{pn.get('name', '')} ({pn.get('effective_from', '')} – {pn.get('ceased_on', '')})"
            for pn in prev_names
        ) if prev_names else "",
    }


def enrich_with_profiles(df, api_key):
    """Fetch full profile for every company and merge into the DataFrame."""
    total = len(df)
    profiles = []

    for i, (_, row) in enumerate(df.iterrows()):
        cn = row["company_number"]
        logger.info(f"  [{i + 1}/{total}] Profile for {cn} — {row['company_name']}")
        profile = fetch_full_profile(cn, api_key)
        if profile:
            profiles.append(profile)
        else:
            # Keep the basic data we already have
            profiles.append({"company_number": cn})
        time.sleep(REQUEST_DELAY)

        # Progress checkpoint
        if (i + 1) % 100 == 0:
            logger.info(f"  ... fetched {i + 1}/{total} profiles")

    profile_df = pd.DataFrame(profiles)

    # Merge: profile columns overwrite advanced-search columns where available
    df = df.set_index("company_number")
    profile_df = profile_df.set_index("company_number")

    # Update existing columns with profile data, add new ones
    for col in profile_df.columns:
        if col in df.columns:
            # Only overwrite where profile has non-empty values
            mask = profile_df[col].fillna("").astype(str).str.strip() != ""
            df.loc[profile_df.index[mask], col] = profile_df.loc[mask, col]
        else:
            df[col] = profile_df[col]

    df = df.reset_index()
    return df


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
def compute_confidence(row):
    """Score 0-100 how likely this company is a plumbing merchant."""
    score = 0.0
    sic_set = set(str(row.get("sic_codes", "")).split(","))
    name_lower = str(row.get("company_name", "")).lower()

    # SIC code signals
    if sic_set & set(TOP_SIC_CODES):
        score += CONFIDENCE_WEIGHTS["has_top3_sic"]

    # Name keyword signals
    if "plumb" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_plumb"]
    if "heating" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_heating"]
    if "bathroom" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_bathroom"]

    return round(score * 100)


# ---------------------------------------------------------------------------
# SIC description lookup
# ---------------------------------------------------------------------------
def add_sic_descriptions(df, sic_lookup):
    """Add a human-readable SIC description column."""
    def describe(sic_str):
        codes = [c.strip() for c in str(sic_str).split(",") if c.strip()]
        parts = []
        for code in codes:
            desc = sic_lookup.get(code, "")
            if desc:
                parts.append(f"{code}: {desc}")
            else:
                parts.append(code)
        return "; ".join(parts)

    df["sic_descriptions"] = df["sic_codes"].apply(describe)
    return df


# ---------------------------------------------------------------------------
# Chain detection
# ---------------------------------------------------------------------------
def _normalise_name(name):
    name = name.strip().upper()
    name = re.sub(
        r"\b(LIMITED|LTD|PLC|LLP|INC|INCORPORATED|CORP|CORPORATION)\b\.?",
        "", name,
    )
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def detect_chains(df):
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Find plumbing merchants from Companies House API"
    )
    parser.add_argument(
        "-o", "--output", default="data/plumbing_merchants_v2.xlsx",
        help="Output Excel file (default: data/plumbing_merchants_v2.xlsx)",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Also save as CSV",
    )
    parser.add_argument(
        "--skip-profiles", action="store_true",
        help="Skip fetching full profiles (faster, less data)",
    )
    args = parser.parse_args()

    api_key = load_api_key()
    sic_lookup = load_sic_descriptions()
    logger.info(f"Loaded {len(sic_lookup)} SIC descriptions")

    all_companies = []

    # --- Strategy 1: Search by SIC codes ---
    print("\n" + "=" * 60)
    print("STEP 1: Fetching by SIC codes")
    print(f"  Codes: {TOP_SIC_CODES}")
    print("=" * 60)
    for sic in TOP_SIC_CODES:
        desc = sic_lookup.get(sic, "")
        logger.info(f"Fetching SIC {sic} ({desc})...")
        results = fetch_by_sic(sic, api_key)
        all_companies.extend(results)
        logger.info(f"  → {len(results)} active companies")

    sic_count = len(all_companies)

    # --- Strategy 2: Search by name keywords ---
    print("\n" + "=" * 60)
    print("STEP 2: Fetching by name keywords")
    print(f"  Keywords: {TOP_KEYWORDS}")
    print("=" * 60)
    for kw in TOP_KEYWORDS:
        logger.info(f"Fetching keyword '{kw}'...")
        results = fetch_by_keyword(kw, api_key)
        all_companies.extend(results)
        logger.info(f"  → {len(results)} active companies")

    total_raw = len(all_companies)

    # --- Combine and deduplicate ---
    df = pd.DataFrame(all_companies)
    if df.empty:
        logger.error("No companies found. Check your API key.")
        return

    before = len(df)
    df = df.drop_duplicates(subset="company_number", keep="first")
    dupes = before - len(df)
    logger.info(f"Removed {dupes} duplicates — {len(df)} unique companies")

    # --- Fetch full company profiles ---
    if not args.skip_profiles:
        print("\n" + "=" * 60)
        print(f"STEP 3: Fetching full profiles for {len(df)} companies")
        print("  (use --skip-profiles to skip this step)")
        print("=" * 60)
        df = enrich_with_profiles(df, api_key)

    # --- SIC descriptions ---
    df = add_sic_descriptions(df, sic_lookup)

    # --- Confidence scoring ---
    df["confidence"] = df.apply(compute_confidence, axis=1)

    # --- Match type classification ---
    has_plumbing_sic = df["sic_codes"].apply(
        lambda x: bool(set(str(x).split(",")) & set(TOP_SIC_CODES))
    )
    has_keyword = df["company_name"].apply(
        lambda n: any(kw in str(n).lower() for kw in TOP_KEYWORDS)
    )
    df["match_type"] = "other"
    df.loc[has_plumbing_sic & has_keyword, "match_type"] = "SIC + keyword"
    df.loc[has_plumbing_sic & ~has_keyword, "match_type"] = "SIC only"
    df.loc[~has_plumbing_sic & has_keyword, "match_type"] = "keyword only"

    # --- Chain detection ---
    df = detect_chains(df)

    # --- Sort by confidence descending ---
    df = df.sort_values("confidence", ascending=False).reset_index(drop=True)

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
    print(f"SIC codes searched:       {TOP_SIC_CODES}")
    print(f"Keywords searched:        {TOP_KEYWORDS}")
    print(f"Total raw results:        {total_raw}")
    print(f"Duplicates removed:       {dupes}")
    print(f"Unique active companies:  {len(df)}")
    if not args.skip_profiles:
        print(f"Full profiles fetched:    YES (all API fields)")
    print()

    print("Confidence distribution:")
    for bucket_label, lo, hi in [
        ("High (70-100%)", 70, 100),
        ("Medium (40-69%)", 40, 69),
        ("Low (1-39%)", 1, 39),
    ]:
        count = ((df["confidence"] >= lo) & (df["confidence"] <= hi)).sum()
        print(f"  {bucket_label:<20} {count:>6}  ({count / len(df) * 100:.1f}%)")
    print()

    print("Match type breakdown:")
    for mtype, count in df["match_type"].value_counts().items():
        print(f"  {mtype:<20} {count:>6}  ({count / len(df) * 100:.1f}%)")
    print()

    for sic in TOP_SIC_CODES:
        desc = sic_lookup.get(sic, "")
        count = df["sic_codes"].str.contains(sic, na=False).sum()
        print(f"  SIC {sic} ({desc[:40]}): {count}")
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
