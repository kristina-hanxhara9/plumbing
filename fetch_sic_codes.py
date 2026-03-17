import os
import re
import time
import logging
import difflib

import pandas as pd
import requests
from dotenv import load_dotenv

from config import (
    API_BASE_URL,
    SEARCH_ENDPOINT,
    COMPANY_ENDPOINT,
    REQUEST_DELAY,
    NAME_COL,
    POSTCODE_COL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_api_key():
    load_dotenv()
    key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not key or key == "your_api_key_here":
        raise ValueError(
            "Set COMPANIES_HOUSE_API_KEY in .env file with a valid API key"
        )
    return key


def _normalise(text):
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _normalise_postcode(pc):
    """Remove spaces and uppercase."""
    return re.sub(r"\s+", "", pc.strip().upper())


def search_company(name, postcode, api_key):
    """Search Companies House for a company by name and postcode.

    Returns dict with company_number, title, postcode_matched or None.
    """
    query = f"{name} {postcode}"
    url = f"{API_BASE_URL}{SEARCH_ENDPOINT}"
    resp = requests.get(
        url, params={"q": query, "items_per_page": 10}, auth=(api_key, "")
    )

    if resp.status_code == 429:
        logger.warning("Rate limited — sleeping 60s")
        time.sleep(60)
        resp = requests.get(
            url, params={"q": query, "items_per_page": 10}, auth=(api_key, "")
        )

    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    norm_pc = _normalise_postcode(postcode)
    norm_name = _normalise(name)

    # Score each result
    best = None
    best_score = -1
    for item in items:
        score = 0
        addr = item.get("address", {}) or {}
        item_pc = addr.get("postal_code", "") or ""
        if _normalise_postcode(item_pc) == norm_pc:
            score += 2  # postcode match is strong signal

        item_title = _normalise(item.get("title", ""))
        name_ratio = difflib.SequenceMatcher(None, norm_name, item_title).ratio()
        score += name_ratio

        if score > best_score:
            best_score = score
            best = item

    if best is None:
        return None

    addr = best.get("address", {}) or {}
    matched_pc = _normalise_postcode(addr.get("postal_code", "") or "") == norm_pc

    return {
        "company_number": best.get("company_number"),
        "title": best.get("title"),
        "postcode_matched": matched_pc,
    }


def get_company_details(company_number, api_key):
    """Fetch company profile to get SIC codes and status."""
    url = f"{API_BASE_URL}{COMPANY_ENDPOINT.format(company_number=company_number)}"
    resp = requests.get(url, auth=(api_key, ""))

    if resp.status_code == 429:
        logger.warning("Rate limited — sleeping 60s")
        time.sleep(60)
        resp = requests.get(url, auth=(api_key, ""))

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    data = resp.json()
    return {
        "sic_codes": data.get("sic_codes", []),
        "company_status": data.get("company_status", "unknown"),
        "company_name": data.get("company_name", ""),
    }


def process_excel(input_path, output_path=None):
    """Read Excel, fetch SIC codes for each row, return enriched DataFrame."""
    api_key = load_api_key()

    df = pd.read_excel(input_path, engine="openpyxl")
    logger.info(f"Loaded {len(df)} rows from {input_path}")

    # Prepare result columns
    ch_names = []
    company_numbers = []
    sic_codes_list = []
    statuses = []
    postcode_matches = []
    api_found = []

    total = len(df)
    for idx, row in df.iterrows():
        name = str(row.iloc[NAME_COL]).strip() if pd.notna(row.iloc[NAME_COL]) else ""
        postcode = (
            str(row.iloc[POSTCODE_COL]).strip()
            if pd.notna(row.iloc[POSTCODE_COL])
            else ""
        )

        logger.info(f"[{idx + 1}/{total}] Searching: {name} | {postcode}")

        if not name or not postcode:
            logger.warning(f"  Skipping — missing name or postcode")
            ch_names.append("")
            company_numbers.append("")
            sic_codes_list.append("")
            statuses.append("")
            postcode_matches.append(False)
            api_found.append(False)
            continue

        try:
            result = search_company(name, postcode, api_key)
            time.sleep(REQUEST_DELAY)

            if result and result["company_number"]:
                details = get_company_details(result["company_number"], api_key)
                time.sleep(REQUEST_DELAY)

                if details:
                    ch_names.append(details["company_name"])
                    company_numbers.append(result["company_number"])
                    sic_codes_list.append(",".join(details["sic_codes"]))
                    statuses.append(details["company_status"])
                    postcode_matches.append(result["postcode_matched"])
                    api_found.append(True)
                    logger.info(
                        f"  Found: {details['company_name']} | "
                        f"SIC: {details['sic_codes']} | "
                        f"Status: {details['company_status']}"
                    )
                else:
                    ch_names.append(result["title"])
                    company_numbers.append(result["company_number"])
                    sic_codes_list.append("")
                    statuses.append("")
                    postcode_matches.append(result["postcode_matched"])
                    api_found.append(False)
                    logger.warning(f"  Company details not found for {result['company_number']}")
            else:
                ch_names.append("")
                company_numbers.append("")
                sic_codes_list.append("")
                statuses.append("")
                postcode_matches.append(False)
                api_found.append(False)
                logger.warning(f"  No search results")

        except Exception as e:
            logger.error(f"  Error: {e}")
            ch_names.append("")
            company_numbers.append("")
            sic_codes_list.append("")
            statuses.append("")
            postcode_matches.append(False)
            api_found.append(False)

        # Save intermediate results every 50 rows
        if (idx + 1) % 50 == 0 and output_path:
            _save_intermediate(df, idx, ch_names, company_numbers, sic_codes_list,
                               statuses, postcode_matches, api_found, output_path)

    df["ch_company_name"] = ch_names
    df["company_number"] = company_numbers
    df["sic_codes"] = sic_codes_list
    df["company_status"] = statuses
    df["postcode_matched"] = postcode_matches
    df["api_match_found"] = api_found

    if output_path:
        df.to_csv(output_path, index=False)
        logger.info(f"Saved enriched data to {output_path}")

    found_count = sum(api_found)
    logger.info(f"API matches: {found_count}/{total}")
    return df


def _save_intermediate(df, current_idx, ch_names, company_numbers, sic_codes_list,
                        statuses, postcode_matches, api_found, output_path):
    """Save partial results so progress isn't lost on failure."""
    partial = df.iloc[: current_idx + 1].copy()
    partial["ch_company_name"] = ch_names
    partial["company_number"] = company_numbers
    partial["sic_codes"] = sic_codes_list
    partial["company_status"] = statuses
    partial["postcode_matched"] = postcode_matches
    partial["api_match_found"] = api_found
    partial.to_csv(output_path, index=False)
    logger.info(f"  Saved intermediate results ({current_idx + 1} rows)")
