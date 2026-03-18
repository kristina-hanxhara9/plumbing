"""Fetch plumbing merchants from Companies House — precision search (v3).

Avoids pulling in 300k+ generic companies from broad SIC codes by applying
keyword filters where needed.

Search strategy (4 passes, deduplicated):
  1. SIC 46740 — ALL active companies (core plumbing wholesale code)
  2. SIC 43220 + plumbing keywords — only plumbing-named installers
  3. SIC 47520 + plumbing keywords — only plumbing-named retailers
  4. Plumbing keywords alone (no SIC) — catches companies under other codes

Features:
  - Full company profiles (all API fields)
  - Confidence scoring (0-100%)
  - Chain detection (same name, multiple postcodes)
  - Buying group detection (known UK plumbing buying groups)
  - SIC descriptions from reference data
  - Match type classification

Usage:
    python fetch_sic_codes_api.py
    python fetch_sic_codes_api.py -o data/plumbing_merchants_v3.xlsx
    python fetch_sic_codes_api.py --skip-profiles
    python fetch_sic_codes_api.py --csv
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
# Search configuration
# ---------------------------------------------------------------------------

# Core SIC code — fetch ALL active companies (no keyword filter needed)
CORE_SIC_CODE = "46740"  # Wholesale of hardware, plumbing and heating equipment

# Broad SIC codes — only fetch companies whose name matches plumbing keywords
FILTERED_SIC_CODES = {
    "43220": "Plumbing, heat and air-conditioning installation",
    "47520": "Retail sale of hardware, paints and glass",
}

# Keywords for filtering broad SIC codes AND for standalone keyword search
PLUMBING_KEYWORDS = ["plumb", "heating", "bathroom", "radiator", "boiler"]

# All SIC codes used across the search (for scoring / classification)
ALL_SIC_CODES = [CORE_SIC_CODE] + list(FILTERED_SIC_CODES.keys())

# ---------------------------------------------------------------------------
# Confidence scoring weights (sum to ~1.0 at maximum)
# ---------------------------------------------------------------------------
CONFIDENCE_WEIGHTS = {
    "has_core_sic": 0.35,        # Has SIC 46740 (wholesale plumbing)
    "has_filtered_sic": 0.15,    # Has SIC 43220 or 47520
    "name_plumb": 0.25,          # Name contains "plumb"
    "name_heating": 0.10,        # Name contains "heating"
    "name_bathroom": 0.05,       # Name contains "bathroom"
    "name_merchant": 0.05,       # Name contains "merchant" / "wholesale" / "trade"
    "name_supplies": 0.05,       # Name contains "supplies" / "supplier"
}

# ---------------------------------------------------------------------------
# Known UK plumbing buying groups
# ---------------------------------------------------------------------------
BUYING_GROUPS = {
    # Group name -> list of known member name patterns (lowercase substrings)
    "IBC (Independent Buying Consortium)": [
        "ibc", "independent buying",
    ],
    "NBG (National Buying Group)": [
        "nbg", "national buying group",
    ],
    "Plumbstock": [
        "plumbstock",
    ],
    "PHG (Plumbing & Heating Group)": [
        "phg", "plumbing & heating group", "plumbing and heating group",
    ],
}

# Known members of major buying groups (normalised name -> group)
# Add more known members as they are discovered
BUYING_GROUP_MEMBERS = {
    "KELLAWAY BUILDING SUPPLIES": "NBG (National Buying Group)",
    "JAMES HARGREAVES": "NBG (National Buying Group)",
    "DAVIS & BOWRING": "NBG (National Buying Group)",
    "MILES PLUMBING SUPPLIES": "NBG (National Buying Group)",
    "NORTHERN PLUMBING SUPPLIES": "NBG (National Buying Group)",
    "GS KELLY": "IBC (Independent Buying Consortium)",
    "NATIONWIDE PLUMBING SUPPLIES": "IBC (Independent Buying Consortium)",
    "J & A YOUNG": "IBC (Independent Buying Consortium)",
    "BEGGS AND PARTNERS": "IBC (Independent Buying Consortium)",
    "HALDANE FISHER": "NBG (National Buying Group)",
    "ROBINSON QUAY": "Plumbstock",
    "TOTAL PLUMBING SUPPLIES": "Plumbstock",
    "NEW QUAY PLUMBING": "Plumbstock",
}


def load_api_key():
    load_dotenv()
    key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not key or key == "your_api_key_here":
        raise ValueError("Set COMPANIES_HOUSE_API_KEY in .env file")
    return key


def load_sic_descriptions():
    """Load SIC code -> description mapping from reference CSV."""
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
            logger.warning(f"Rate limited -- sleeping {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


# ---------------------------------------------------------------------------
# Advanced Search (bulk discovery)
# ---------------------------------------------------------------------------
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


def _paginated_search(params_base, api_key, label=""):
    """Run a paginated Advanced Search query, returning all matching companies."""
    url = f"{API_BASE_URL}{ADVANCED_SEARCH_ENDPOINT}"
    companies = []
    start_index = 0

    while start_index < MAX_RESULTS:
        params = {**params_base, "size": PAGE_SIZE, "start_index": start_index}
        resp = _api_get(url, params, api_key)
        if resp.status_code != 200:
            logger.error(f"API error {resp.status_code} for {label}: {resp.text[:200]}")
            break

        data = resp.json()
        items = data.get("items", [])
        total_hits = data.get("hits", 0)
        if not items:
            break

        for item in items:
            companies.append(_extract_advanced(item, source=label))

        logger.info(f"  {label}: fetched {len(companies)} / {total_hits}")
        start_index += PAGE_SIZE
        time.sleep(REQUEST_DELAY)
        if start_index >= total_hits:
            break

    return companies


def fetch_by_sic(sic_code, api_key):
    """Fetch ALL active companies for a SIC code."""
    return _paginated_search(
        {"sic_codes": sic_code, "company_status": "active"},
        api_key,
        label=f"SIC:{sic_code}",
    )


def fetch_by_sic_and_keyword(sic_code, keyword, api_key):
    """Fetch active companies matching BOTH a SIC code AND a name keyword."""
    return _paginated_search(
        {
            "sic_codes": sic_code,
            "company_name_includes": keyword,
            "company_status": "active",
        },
        api_key,
        label=f"SIC:{sic_code}+kw:{keyword}",
    )


def fetch_by_keyword(keyword, api_key):
    """Fetch all active companies matching a name keyword (any SIC)."""
    return _paginated_search(
        {"company_name_includes": keyword, "company_status": "active"},
        api_key,
        label=f"keyword:{keyword}",
    )


# ---------------------------------------------------------------------------
# Full Company Profile
# ---------------------------------------------------------------------------
def fetch_full_profile(company_number, api_key):
    """Fetch the complete company profile -- every field the API returns."""
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

        # Registered office address
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
            f"{pn.get('name', '')} ({pn.get('effective_from', '')} - {pn.get('ceased_on', '')})"
            for pn in prev_names
        ) if prev_names else "",
    }


def enrich_with_profiles(df, api_key):
    """Fetch full profile for every company and merge into the DataFrame."""
    total = len(df)
    profiles = []

    for i, (_, row) in enumerate(df.iterrows()):
        cn = row["company_number"]
        logger.info(f"  [{i + 1}/{total}] Profile for {cn} -- {row['company_name']}")
        profile = fetch_full_profile(cn, api_key)
        if profile:
            profiles.append(profile)
        else:
            profiles.append({"company_number": cn})
        time.sleep(REQUEST_DELAY)

        if (i + 1) % 100 == 0:
            logger.info(f"  ... fetched {i + 1}/{total} profiles")

    profile_df = pd.DataFrame(profiles)

    df = df.set_index("company_number")
    profile_df = profile_df.set_index("company_number")

    for col in profile_df.columns:
        if col in df.columns:
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
    if CORE_SIC_CODE in sic_set:
        score += CONFIDENCE_WEIGHTS["has_core_sic"]
    if sic_set & set(FILTERED_SIC_CODES.keys()):
        score += CONFIDENCE_WEIGHTS["has_filtered_sic"]

    # Name keyword signals
    if "plumb" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_plumb"]
    if "heating" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_heating"]
    if "bathroom" in name_lower:
        score += CONFIDENCE_WEIGHTS["name_bathroom"]
    if any(kw in name_lower for kw in ("merchant", "wholesale", "trade")):
        score += CONFIDENCE_WEIGHTS["name_merchant"]
    if "suppli" in name_lower:  # catches supplies, supplier, supply
        score += CONFIDENCE_WEIGHTS["name_supplies"]

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
    """Normalise company name for chain/group matching."""
    name = name.strip().upper()
    name = re.sub(
        r"\b(LIMITED|LTD|PLC|LLP|INC|INCORPORATED|CORP|CORPORATION)\b\.?",
        "", name,
    )
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def detect_chains(df):
    """Detect chain businesses: same normalised name, different postcodes.

    Adds columns:
        chain_name (str): Normalised name used for grouping.
        is_chain (bool): True if the company name appears at multiple postcodes.
        chain_branch_count (int): How many branches this chain has.
    """
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

    chain_count = df.loc[df["is_chain"], "chain_name"].nunique()
    chain_branches = df["is_chain"].sum()
    logger.info(f"Chain detection: {chain_count} chains ({chain_branches} branches)")

    return df


# ---------------------------------------------------------------------------
# Buying group detection
# ---------------------------------------------------------------------------
def detect_buying_groups(df):
    """Detect membership of known UK plumbing buying groups.

    Uses two methods:
      1. Name pattern matching (company name contains group-related terms)
      2. Known member lookup (manually curated list of known members)

    Adds columns:
        buying_group (str): Name of the buying group, or empty string.
        is_buying_group_member (bool): True if a buying group was identified.
    """
    df = df.copy()
    df["buying_group"] = ""

    for _, row in df.iterrows():
        idx = row.name
        name_lower = str(row.get("company_name", "")).lower()
        norm_name = row.get("chain_name", _normalise_name(str(row.get("company_name", ""))))

        # Method 1: Check name against buying group patterns
        for group_name, patterns in BUYING_GROUPS.items():
            if any(pat in name_lower for pat in patterns if pat):
                df.at[idx, "buying_group"] = group_name
                break

        # Method 2: Check against known members list
        if not df.at[idx, "buying_group"]:
            for member_name, group_name in BUYING_GROUP_MEMBERS.items():
                if member_name and member_name in norm_name:
                    df.at[idx, "buying_group"] = group_name
                    break

    df["is_buying_group_member"] = df["buying_group"].astype(str).str.strip() != ""

    group_count = df["is_buying_group_member"].sum()
    logger.info(f"Buying group detection: {group_count} members identified")

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Fetch plumbing merchants from Companies House API (precision search)"
    )
    parser.add_argument(
        "-o", "--output", default="data/plumbing_merchants_v3.xlsx",
        help="Output Excel file (default: data/plumbing_merchants_v3.xlsx)",
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

    # --- Strategy 1: Core SIC code (46740) — ALL active companies ---
    print("\n" + "=" * 60)
    print(f"STEP 1: SIC {CORE_SIC_CODE} -- ALL active companies")
    print("  (Wholesale of hardware, plumbing and heating equipment)")
    print("=" * 60)
    results = fetch_by_sic(CORE_SIC_CODE, api_key)
    all_companies.extend(results)
    logger.info(f"  -> {len(results)} active companies")

    step1_count = len(all_companies)

    # --- Strategy 2: Filtered SIC codes + plumbing keywords ---
    print("\n" + "=" * 60)
    print("STEP 2: Filtered SIC codes + plumbing keywords")
    print(f"  SIC codes: {list(FILTERED_SIC_CODES.keys())}")
    print(f"  Keywords:  {PLUMBING_KEYWORDS}")
    print("=" * 60)
    for sic_code, sic_desc in FILTERED_SIC_CODES.items():
        for keyword in PLUMBING_KEYWORDS:
            logger.info(f"Fetching SIC {sic_code} + keyword '{keyword}'...")
            results = fetch_by_sic_and_keyword(sic_code, keyword, api_key)
            all_companies.extend(results)
            logger.info(f"  -> {len(results)} companies")

    step2_count = len(all_companies) - step1_count

    # --- Strategy 3: Keywords alone (no SIC filter) ---
    print("\n" + "=" * 60)
    print("STEP 3: Plumbing keywords alone (any SIC code)")
    print(f"  Keywords: {PLUMBING_KEYWORDS}")
    print("=" * 60)
    for keyword in PLUMBING_KEYWORDS:
        logger.info(f"Fetching keyword '{keyword}' (all SIC codes)...")
        results = fetch_by_keyword(keyword, api_key)
        all_companies.extend(results)
        logger.info(f"  -> {len(results)} companies")

    step3_count = len(all_companies) - step1_count - step2_count
    total_raw = len(all_companies)

    # --- Combine and deduplicate ---
    df = pd.DataFrame(all_companies)
    if df.empty:
        logger.error("No companies found. Check your API key.")
        return

    before = len(df)
    df = df.drop_duplicates(subset="company_number", keep="first")
    dupes = before - len(df)
    logger.info(f"Removed {dupes} duplicates -- {len(df)} unique companies")

    # --- Fetch full company profiles ---
    if not args.skip_profiles:
        print("\n" + "=" * 60)
        print(f"STEP 4: Fetching full profiles for {len(df)} companies")
        print("  (use --skip-profiles to skip this step)")
        print("=" * 60)
        df = enrich_with_profiles(df, api_key)

    # --- SIC descriptions ---
    df = add_sic_descriptions(df, sic_lookup)

    # --- Confidence scoring ---
    df["confidence"] = df.apply(compute_confidence, axis=1)

    # --- Match type classification ---
    has_core_sic = df["sic_codes"].apply(
        lambda x: CORE_SIC_CODE in set(str(x).split(","))
    )
    has_filtered_sic = df["sic_codes"].apply(
        lambda x: bool(set(str(x).split(",")) & set(FILTERED_SIC_CODES.keys()))
    )
    has_keyword = df["company_name"].apply(
        lambda n: any(kw in str(n).lower() for kw in PLUMBING_KEYWORDS)
    )
    df["match_type"] = "other"
    df.loc[has_core_sic & has_keyword, "match_type"] = "core SIC + keyword"
    df.loc[has_core_sic & ~has_keyword, "match_type"] = "core SIC only"
    df.loc[~has_core_sic & has_filtered_sic & has_keyword, "match_type"] = "filtered SIC + keyword"
    df.loc[~has_core_sic & ~has_filtered_sic & has_keyword, "match_type"] = "keyword only"

    # --- Chain detection ---
    df = detect_chains(df)

    # --- Buying group detection ---
    df = detect_buying_groups(df)

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
    print(f"Core SIC code:            {CORE_SIC_CODE}")
    print(f"Filtered SIC codes:       {list(FILTERED_SIC_CODES.keys())}")
    print(f"Keywords:                 {PLUMBING_KEYWORDS}")
    print()
    print(f"Step 1 (core SIC):        {step1_count} raw results")
    print(f"Step 2 (SIC + keywords):  {step2_count} raw results")
    print(f"Step 3 (keywords only):   {step3_count} raw results")
    print(f"Total raw results:        {total_raw}")
    print(f"Duplicates removed:       {dupes}")
    print(f"Unique active companies:  {len(df)}")
    if not args.skip_profiles:
        print(f"Full profiles fetched:    YES (all API fields)")
    print()

    # Confidence distribution
    print("Confidence distribution:")
    for bucket_label, lo, hi in [
        ("High (70-100%)", 70, 100),
        ("Medium (40-69%)", 40, 69),
        ("Low (1-39%)", 1, 39),
    ]:
        count = ((df["confidence"] >= lo) & (df["confidence"] <= hi)).sum()
        pct = count / len(df) * 100 if len(df) > 0 else 0
        print(f"  {bucket_label:<20} {count:>6}  ({pct:.1f}%)")
    print()

    # Match type breakdown
    print("Match type breakdown:")
    for mtype, count in df["match_type"].value_counts().items():
        pct = count / len(df) * 100 if len(df) > 0 else 0
        print(f"  {mtype:<25} {count:>6}  ({pct:.1f}%)")
    print()

    # SIC code breakdown
    print("SIC code breakdown:")
    for sic in ALL_SIC_CODES:
        desc = sic_lookup.get(sic, "")
        count = df["sic_codes"].str.contains(sic, na=False).sum()
        print(f"  SIC {sic} ({desc[:40]:<40}): {count}")
    print()

    # Chain businesses
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
                print(f"    {name}: {count} branches")
        print()

    # Buying groups
    if "is_buying_group_member" in df.columns:
        bg_count = df["is_buying_group_member"].sum()
        print(f"Buying group members:     {bg_count}")
        if bg_count > 0:
            for group, count in df.loc[df["is_buying_group_member"], "buying_group"].value_counts().items():
                print(f"    {group}: {count} companies")
        print()

    print(f"Output: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
