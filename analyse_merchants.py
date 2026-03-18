"""Analyse plumbing merchants found by fetch_sic_codes_api.py.

Produces a detailed breakdown of the merchant dataset:
  - How they were found (search strategy breakdown)
  - Geographic distribution (by region, postcode area)
  - Company type and age analysis
  - Chain vs independent breakdown
  - Buying group membership
  - SIC code distribution
  - Confidence score analysis
  - Top companies by branch count

Outputs:
  - Console summary report
  - Multi-sheet Excel workbook with all analysis tables
  - Optional CSV exports

Usage:
    python analyse_merchants.py
    python analyse_merchants.py -i data/plumbing_merchants_v3.xlsx
    python analyse_merchants.py -i data/plumbing_merchants_v3.xlsx --csv
"""
import argparse
import logging
import os
import re

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# UK regions by postcode area prefix (first 1-2 letters)
POSTCODE_REGIONS = {
    "AB": "Scotland", "AL": "East of England", "B": "West Midlands",
    "BA": "South West", "BB": "North West", "BD": "Yorkshire",
    "BH": "South West", "BL": "North West", "BN": "South East",
    "BR": "London", "BS": "South West", "BT": "Northern Ireland",
    "CA": "North West", "CB": "East of England", "CF": "Wales",
    "CH": "North West", "CM": "East of England", "CO": "East of England",
    "CR": "London", "CT": "South East", "CV": "West Midlands",
    "CW": "North West", "DA": "London", "DD": "Scotland",
    "DE": "East Midlands", "DG": "Scotland", "DH": "North East",
    "DL": "North East", "DN": "Yorkshire", "DT": "South West",
    "DY": "West Midlands", "E": "London", "EC": "London",
    "EH": "Scotland", "EN": "London", "EX": "South West",
    "FK": "Scotland", "FY": "North West", "G": "Scotland",
    "GL": "South West", "GU": "South East", "HA": "London",
    "HD": "Yorkshire", "HG": "Yorkshire", "HP": "South East",
    "HR": "West Midlands", "HS": "Scotland", "HU": "Yorkshire",
    "HX": "Yorkshire", "IG": "London", "IP": "East of England",
    "IV": "Scotland", "KA": "Scotland", "KT": "London",
    "KW": "Scotland", "KY": "Scotland", "L": "North West",
    "LA": "North West", "LD": "Wales", "LE": "East Midlands",
    "LL": "Wales", "LN": "East Midlands", "LS": "Yorkshire",
    "LU": "East of England", "M": "North West", "ME": "South East",
    "MK": "South East", "ML": "Scotland", "N": "London",
    "NE": "North East", "NG": "East Midlands", "NN": "East Midlands",
    "NP": "Wales", "NR": "East of England", "NW": "London",
    "OL": "North West", "OX": "South East", "PA": "Scotland",
    "PE": "East of England", "PH": "Scotland", "PL": "South West",
    "PO": "South East", "PR": "North West", "RG": "South East",
    "RH": "South East", "RM": "London", "S": "Yorkshire",
    "SA": "Wales", "SE": "London", "SG": "East of England",
    "SK": "North West", "SL": "South East", "SM": "London",
    "SN": "South West", "SO": "South East", "SP": "South West",
    "SR": "North East", "SS": "East of England", "ST": "West Midlands",
    "SW": "London", "SY": "Wales", "TA": "South West",
    "TD": "Scotland", "TF": "West Midlands", "TN": "South East",
    "TQ": "South West", "TR": "South West", "TS": "North East",
    "TW": "London", "UB": "London", "W": "London",
    "WA": "North West", "WC": "London", "WD": "East of England",
    "WF": "Yorkshire", "WN": "North West", "WR": "West Midlands",
    "WS": "West Midlands", "WV": "West Midlands", "YO": "Yorkshire",
    "ZE": "Scotland",
}


def extract_postcode_area(postal_code):
    """Extract the letter prefix from a UK postcode (e.g. 'SW1A 1AA' -> 'SW')."""
    if not postal_code or pd.isna(postal_code):
        return ""
    match = re.match(r"^([A-Z]{1,2})", str(postal_code).strip().upper())
    return match.group(1) if match else ""


def postcode_to_region(postal_code):
    """Map a UK postcode to its region."""
    area = extract_postcode_area(postal_code)
    if not area:
        return "Unknown"
    # Try 2-letter match first, then 1-letter
    return POSTCODE_REGIONS.get(area, POSTCODE_REGIONS.get(area[0], "Unknown"))


def load_merchants(path):
    """Load the merchant list from Excel or CSV."""
    if path.endswith(".xlsx"):
        df = pd.read_excel(path, engine="openpyxl")
    else:
        df = pd.read_csv(path)
    logger.info(f"Loaded {len(df)} merchants from {path}")
    return df


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyse_search_strategy(df):
    """Break down how companies were discovered."""
    results = {}

    # By source field (which search pass found them first)
    if "source" in df.columns:
        source_counts = df["source"].value_counts()
        results["by_source"] = source_counts.to_dict()

    # By match type
    if "match_type" in df.columns:
        match_counts = df["match_type"].value_counts()
        results["by_match_type"] = match_counts.to_dict()

    return results


def analyse_geography(df):
    """Geographic distribution by region and postcode area."""
    df = df.copy()

    # Add region
    df["region_derived"] = df["postal_code"].apply(postcode_to_region)
    df["postcode_area"] = df["postal_code"].apply(extract_postcode_area)

    # Region counts
    region_counts = (
        df["region_derived"].value_counts()
        .reset_index()
        .rename(columns={"index": "region", "region_derived": "region", "count": "companies"})
    )

    # Postcode area counts (top 30)
    area_counts = (
        df["postcode_area"].value_counts()
        .head(30)
        .reset_index()
        .rename(columns={"index": "postcode_area", "postcode_area": "postcode_area", "count": "companies"})
    )

    # Companies with/without postcode
    has_postcode = df["postal_code"].notna() & (df["postal_code"].astype(str).str.strip() != "")

    return {
        "by_region": region_counts,
        "by_postcode_area": area_counts,
        "with_postcode": has_postcode.sum(),
        "without_postcode": (~has_postcode).sum(),
        "df_with_region": df,
    }


def analyse_company_types(df):
    """Break down by company type, status, and age."""
    results = {}

    # Company type
    if "company_type" in df.columns:
        results["by_type"] = df["company_type"].value_counts().to_dict()

    # Company status
    if "company_status" in df.columns:
        results["by_status"] = df["company_status"].value_counts().to_dict()

    # Age analysis
    if "date_of_creation" in df.columns:
        dates = pd.to_datetime(df["date_of_creation"], errors="coerce")
        valid = dates.dropna()
        if not valid.empty:
            now = pd.Timestamp.now()
            ages = (now - valid).dt.days / 365.25
            results["age_stats"] = {
                "oldest_years": round(ages.max(), 1),
                "newest_years": round(ages.min(), 1),
                "median_years": round(ages.median(), 1),
                "mean_years": round(ages.mean(), 1),
            }

            # Age buckets
            buckets = pd.cut(ages, bins=[0, 2, 5, 10, 20, 50, 200],
                             labels=["0-2y", "2-5y", "5-10y", "10-20y", "20-50y", "50y+"])
            results["by_age_bucket"] = buckets.value_counts().sort_index().to_dict()

    return results


def analyse_chains(df):
    """Detailed chain analysis."""
    results = {}

    if "is_chain" not in df.columns:
        return results

    chains_df = df[df["is_chain"] == True]
    independents_df = df[df["is_chain"] != True]

    results["total_chains"] = chains_df["chain_name"].nunique() if "chain_name" in chains_df.columns else 0
    results["total_chain_branches"] = len(chains_df)
    results["total_independents"] = len(independents_df)

    # Top chains by branch count
    if "chain_name" in chains_df.columns and "chain_branch_count" in chains_df.columns:
        top_chains = (
            chains_df.groupby("chain_name")
            .agg(
                branches=("chain_branch_count", "first"),
                sample_postcode=("postal_code", "first"),
            )
            .sort_values("branches", ascending=False)
            .head(25)
        )
        results["top_chains"] = top_chains

    # Chain size distribution
    if "chain_branch_count" in chains_df.columns and not chains_df.empty:
        chain_sizes = (
            chains_df.groupby("chain_name")["chain_branch_count"]
            .first()
        )
        size_buckets = pd.cut(chain_sizes, bins=[1, 2, 5, 10, 25, 50, 500],
                              labels=["2", "3-5", "6-10", "11-25", "26-50", "50+"])
        results["chain_size_distribution"] = size_buckets.value_counts().sort_index().to_dict()

    return results


def analyse_buying_groups(df):
    """Buying group membership analysis."""
    results = {}

    if "is_buying_group_member" not in df.columns:
        return results

    members_df = df[df["is_buying_group_member"] == True]
    results["total_members"] = len(members_df)
    results["non_members"] = len(df) - len(members_df)

    if "buying_group" in members_df.columns and not members_df.empty:
        group_counts = members_df["buying_group"].value_counts()
        results["by_group"] = group_counts.to_dict()

        # Members per group with their names
        group_details = {}
        for group_name in group_counts.index:
            group_members = members_df[members_df["buying_group"] == group_name]
            group_details[group_name] = {
                "count": len(group_members),
                "members": group_members["company_name"].tolist()[:20],
            }
        results["group_details"] = group_details

    return results


def analyse_sic_codes(df):
    """SIC code distribution across the dataset."""
    results = {}

    if "sic_codes" not in df.columns:
        return results

    # Explode SIC codes
    all_sics = []
    for _, row in df.iterrows():
        codes = str(row.get("sic_codes", "")).split(",")
        for code in codes:
            code = code.strip()
            if code and code != "nan":
                all_sics.append(code)

    sic_counts = pd.Series(all_sics).value_counts().head(30)
    results["top_sic_codes"] = sic_counts.to_dict()

    # Companies with multiple SIC codes
    multi_sic = df["sic_codes"].apply(
        lambda x: len([c for c in str(x).split(",") if c.strip() and c.strip() != "nan"])
    )
    results["sic_count_distribution"] = {
        "1 SIC code": (multi_sic == 1).sum(),
        "2 SIC codes": (multi_sic == 2).sum(),
        "3+ SIC codes": (multi_sic >= 3).sum(),
        "No SIC code": (multi_sic == 0).sum(),
    }

    return results


def analyse_confidence(df):
    """Confidence score distribution and insights."""
    results = {}

    if "confidence" not in df.columns:
        return results

    results["stats"] = {
        "mean": round(df["confidence"].mean(), 1),
        "median": round(df["confidence"].median(), 1),
        "min": int(df["confidence"].min()),
        "max": int(df["confidence"].max()),
    }

    # Buckets
    buckets = {
        "High (70-100%)": ((df["confidence"] >= 70) & (df["confidence"] <= 100)).sum(),
        "Medium (40-69%)": ((df["confidence"] >= 40) & (df["confidence"] < 70)).sum(),
        "Low (1-39%)": ((df["confidence"] >= 1) & (df["confidence"] < 40)).sum(),
        "Zero (0%)": (df["confidence"] == 0).sum(),
    }
    results["buckets"] = buckets

    # High confidence sample
    high_conf = df[df["confidence"] >= 70].head(10)
    if not high_conf.empty:
        results["high_confidence_sample"] = high_conf[
            ["company_name", "company_number", "confidence", "sic_codes", "postal_code"]
        ].to_dict(orient="records")

    return results


def analyse_accounts(df):
    """Accounts and filing status analysis."""
    results = {}

    if "last_accounts_type" in df.columns:
        results["by_accounts_type"] = df["last_accounts_type"].value_counts().to_dict()

    if "accounts_overdue" in df.columns:
        overdue = df["accounts_overdue"].apply(
            lambda x: str(x).lower() in ("true", "1", "yes")
        )
        results["accounts_overdue"] = overdue.sum()
        results["accounts_current"] = (~overdue).sum()

    if "has_charges" in df.columns:
        has_charges = df["has_charges"].apply(
            lambda x: str(x).lower() in ("true", "1", "yes")
        )
        results["with_charges"] = has_charges.sum()

    if "has_insolvency_history" in df.columns:
        insolvent = df["has_insolvency_history"].apply(
            lambda x: str(x).lower() in ("true", "1", "yes")
        )
        results["with_insolvency_history"] = insolvent.sum()

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(df, search, geo, company_types, chains, buying_groups,
                 sic_codes, confidence, accounts):
    """Print a comprehensive console report."""
    total = len(df)

    print("\n" + "=" * 70)
    print("  PLUMBING MERCHANTS ANALYSIS REPORT")
    print("=" * 70)
    print(f"\nTotal companies analysed: {total:,}")

    # --- Search strategy ---
    print("\n" + "-" * 70)
    print("  HOW THEY WERE FOUND")
    print("-" * 70)
    if "by_match_type" in search:
        for mtype, count in search["by_match_type"].items():
            pct = count / total * 100
            bar = "#" * int(pct / 2)
            print(f"  {mtype:<25} {count:>6}  ({pct:5.1f}%)  {bar}")

    if "by_source" in search:
        print("\n  First-match source (which search pass found them):")
        for source, count in list(search["by_source"].items())[:15]:
            pct = count / total * 100
            print(f"    {source:<35} {count:>6}  ({pct:5.1f}%)")

    # --- Geography ---
    print("\n" + "-" * 70)
    print("  GEOGRAPHIC DISTRIBUTION")
    print("-" * 70)
    print(f"  With postcode:    {geo['with_postcode']:>6}")
    print(f"  Without postcode: {geo['without_postcode']:>6}")

    if not geo["by_region"].empty:
        print("\n  By region:")
        for _, row in geo["by_region"].iterrows():
            region = row.iloc[0]
            count = row.iloc[1]
            pct = count / total * 100
            bar = "#" * int(pct / 2)
            print(f"    {region:<20} {count:>6}  ({pct:5.1f}%)  {bar}")

    if not geo["by_postcode_area"].empty:
        print("\n  Top 15 postcode areas:")
        for _, row in geo["by_postcode_area"].head(15).iterrows():
            area = row.iloc[0]
            count = row.iloc[1]
            print(f"    {area:<5} {count:>6}")

    # --- Company types ---
    print("\n" + "-" * 70)
    print("  COMPANY TYPES & AGE")
    print("-" * 70)
    if "by_type" in company_types:
        print("  By type:")
        for ctype, count in list(company_types["by_type"].items())[:10]:
            print(f"    {ctype:<30} {count:>6}")

    if "age_stats" in company_types:
        stats = company_types["age_stats"]
        print(f"\n  Age: median {stats['median_years']}y, "
              f"mean {stats['mean_years']}y, "
              f"oldest {stats['oldest_years']}y, "
              f"newest {stats['newest_years']}y")

    if "by_age_bucket" in company_types:
        print("  By age:")
        for bucket, count in company_types["by_age_bucket"].items():
            pct = count / total * 100
            print(f"    {bucket:<10} {count:>6}  ({pct:5.1f}%)")

    # --- Chains ---
    print("\n" + "-" * 70)
    print("  CHAINS vs INDEPENDENTS")
    print("-" * 70)
    if chains:
        print(f"  Chain businesses:      {chains.get('total_chains', 0)} chains "
              f"({chains.get('total_chain_branches', 0)} branches)")
        print(f"  Independent businesses: {chains.get('total_independents', 0)}")

        if "chain_size_distribution" in chains:
            print("\n  Chain size distribution (branches):")
            for size, count in chains["chain_size_distribution"].items():
                print(f"    {size} branches: {count} chains")

        if "top_chains" in chains:
            print("\n  Top 15 chains:")
            for name, row in chains["top_chains"].head(15).iterrows():
                print(f"    {name:<40} {int(row['branches']):>4} branches")

    # --- Buying groups ---
    print("\n" + "-" * 70)
    print("  BUYING GROUPS")
    print("-" * 70)
    if buying_groups:
        print(f"  Identified members:  {buying_groups.get('total_members', 0)}")
        print(f"  Not identified:      {buying_groups.get('non_members', 0)}")

        if "by_group" in buying_groups:
            print("\n  By group:")
            for group, count in buying_groups["by_group"].items():
                print(f"    {group:<45} {count:>4}")

        if "group_details" in buying_groups:
            print("\n  Member samples:")
            for group, details in buying_groups["group_details"].items():
                print(f"\n    {group}:")
                for member in details["members"][:5]:
                    print(f"      - {member}")
    else:
        print("  No buying group data available")

    # --- SIC codes ---
    print("\n" + "-" * 70)
    print("  SIC CODE DISTRIBUTION")
    print("-" * 70)
    if "top_sic_codes" in sic_codes:
        print("  Top 15 SIC codes:")
        for sic, count in list(sic_codes["top_sic_codes"].items())[:15]:
            pct = count / total * 100
            print(f"    {sic:<10} {count:>6}  ({pct:5.1f}%)")

    if "sic_count_distribution" in sic_codes:
        print("\n  SIC codes per company:")
        for label, count in sic_codes["sic_count_distribution"].items():
            print(f"    {label:<15} {count:>6}")

    # --- Confidence ---
    print("\n" + "-" * 70)
    print("  CONFIDENCE SCORES")
    print("-" * 70)
    if "stats" in confidence:
        s = confidence["stats"]
        print(f"  Mean: {s['mean']}%  Median: {s['median']}%  "
              f"Range: {s['min']}% - {s['max']}%")

    if "buckets" in confidence:
        print("\n  Distribution:")
        for label, count in confidence["buckets"].items():
            pct = count / total * 100
            bar = "#" * int(pct / 2)
            print(f"    {label:<20} {count:>6}  ({pct:5.1f}%)  {bar}")

    # --- Accounts ---
    print("\n" + "-" * 70)
    print("  ACCOUNTS & FILING STATUS")
    print("-" * 70)
    if accounts:
        if "by_accounts_type" in accounts:
            print("  Accounts type:")
            for atype, count in list(accounts["by_accounts_type"].items())[:10]:
                print(f"    {atype:<30} {count:>6}")

        if "accounts_overdue" in accounts:
            print(f"\n  Accounts overdue: {accounts['accounts_overdue']}")
        if "with_charges" in accounts:
            print(f"  Has charges:      {accounts['with_charges']}")
        if "with_insolvency_history" in accounts:
            print(f"  Insolvency hist:  {accounts['with_insolvency_history']}")

    print("\n" + "=" * 70)


def save_excel_report(output_path, df, geo, chains, buying_groups, sic_codes, confidence):
    """Save multi-sheet Excel workbook with all analysis tables."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Sheet 1: Full data with derived region
        df_with_region = geo.get("df_with_region", df)
        df_with_region.to_excel(writer, sheet_name="All Companies", index=False)

        # Sheet 2: By region
        if not geo["by_region"].empty:
            geo["by_region"].to_excel(writer, sheet_name="By Region", index=False)

        # Sheet 3: By postcode area
        if not geo["by_postcode_area"].empty:
            geo["by_postcode_area"].to_excel(writer, sheet_name="By Postcode Area", index=False)

        # Sheet 4: Chains
        if "top_chains" in chains and chains["top_chains"] is not None:
            chains["top_chains"].to_excel(writer, sheet_name="Top Chains")

        # Sheet 5: Buying group members
        if "is_buying_group_member" in df.columns:
            members = df[df["is_buying_group_member"] == True]
            if not members.empty:
                cols = ["company_name", "company_number", "buying_group",
                        "postal_code", "sic_codes", "confidence"]
                available_cols = [c for c in cols if c in members.columns]
                members[available_cols].to_excel(
                    writer, sheet_name="Buying Group Members", index=False
                )

        # Sheet 6: SIC code breakdown
        if "top_sic_codes" in sic_codes:
            sic_df = pd.DataFrame(
                list(sic_codes["top_sic_codes"].items()),
                columns=["sic_code", "company_count"]
            )
            sic_df.to_excel(writer, sheet_name="SIC Codes", index=False)

        # Sheet 7: High confidence companies
        if "high_confidence_sample" in confidence:
            high_df = pd.DataFrame(confidence["high_confidence_sample"])
            high_df.to_excel(writer, sheet_name="High Confidence", index=False)

    logger.info(f"Saved analysis workbook to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Analyse plumbing merchants found by fetch_sic_codes_api.py"
    )
    parser.add_argument(
        "-i", "--input", default="data/plumbing_merchants_v3.xlsx",
        help="Input merchant list (default: data/plumbing_merchants_v3.xlsx)",
    )
    parser.add_argument(
        "-o", "--output", default="data/merchant_analysis.xlsx",
        help="Output analysis workbook (default: data/merchant_analysis.xlsx)",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Also export key tables as CSV files",
    )
    args = parser.parse_args()

    # Load data
    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}")
        print("Run fetch_sic_codes_api.py first to generate the merchant list.")
        return

    df = load_merchants(args.input)

    if df.empty:
        print("No data to analyse.")
        return

    # Run all analyses
    search = analyse_search_strategy(df)
    geo = analyse_geography(df)
    company_types = analyse_company_types(df)
    chains_result = analyse_chains(df)
    buying_groups = analyse_buying_groups(df)
    sic_codes = analyse_sic_codes(df)
    confidence = analyse_confidence(df)
    accounts = analyse_accounts(df)

    # Print console report
    print_report(df, search, geo, company_types, chains_result,
                 buying_groups, sic_codes, confidence, accounts)

    # Save Excel workbook
    save_excel_report(args.output, df, geo, chains_result, buying_groups,
                      sic_codes, confidence)

    # Optional CSV exports
    if args.csv:
        csv_dir = os.path.splitext(args.output)[0] + "_csv"
        os.makedirs(csv_dir, exist_ok=True)

        if not geo["by_region"].empty:
            geo["by_region"].to_csv(os.path.join(csv_dir, "by_region.csv"), index=False)
        if not geo["by_postcode_area"].empty:
            geo["by_postcode_area"].to_csv(os.path.join(csv_dir, "by_postcode_area.csv"), index=False)
        if "top_chains" in chains_result and chains_result["top_chains"] is not None:
            chains_result["top_chains"].to_csv(os.path.join(csv_dir, "top_chains.csv"))
        if "top_sic_codes" in sic_codes:
            pd.DataFrame(
                list(sic_codes["top_sic_codes"].items()),
                columns=["sic_code", "company_count"]
            ).to_csv(os.path.join(csv_dir, "sic_codes.csv"), index=False)

        logger.info(f"CSV exports saved to {csv_dir}/")

    print(f"\nAnalysis saved to: {args.output}")


if __name__ == "__main__":
    main()
