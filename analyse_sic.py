"""Analyse SIC code frequency from existing output files.

Usage:
    python analyse_sic.py                          # uses data/output.csv
    python analyse_sic.py -i data/output_enriched.csv
    python analyse_sic.py -i data/output.csv --top 30
"""
import argparse
import os
import sys

import pandas as pd

# Path to the official UK SIC 2007 condensed reference (from Companies House / ONS)
SIC_REFERENCE_PATH = os.path.join(os.path.dirname(__file__), "data", "sic_reference.csv")


def load_sic_descriptions():
    """Load SIC code descriptions from the official UK SIC 2007 reference file."""
    if not os.path.exists(SIC_REFERENCE_PATH):
        print(f"WARNING: SIC reference file not found at {SIC_REFERENCE_PATH}")
        return {}
    ref = pd.read_csv(SIC_REFERENCE_PATH, dtype=str)
    return dict(zip(ref["sic_code"].str.strip(), ref["sic_description"].str.strip()))


def analyse(df, top_n=20):
    """Analyse SIC code frequency from a DataFrame with a sic_codes column."""
    sic_series = df["sic_codes"].fillna("").astype(str)
    all_codes = []
    for codes_str in sic_series:
        for code in codes_str.split(","):
            code = code.strip()
            if code:
                all_codes.append(code)

    if not all_codes:
        print("No SIC codes found in the data.")
        return

    code_counts = pd.Series(all_codes).value_counts().reset_index()
    code_counts.columns = ["sic_code", "count"]
    total_companies = (sic_series.str.strip().ne("")).sum()
    code_counts["percentage"] = (code_counts["count"] / total_companies * 100).round(1)

    sic_descriptions = load_sic_descriptions()
    code_counts["description"] = code_counts["sic_code"].map(
        lambda x: sic_descriptions.get(x, sic_descriptions.get(x.lstrip("0"), ""))
    )

    print(f"\nTotal companies with SIC data: {total_companies}")
    print(f"Total SIC code assignments:    {len(all_codes)}")
    print(f"Unique SIC codes:              {code_counts.shape[0]}")

    print("\n" + "=" * 80)
    print(f"TOP {top_n} SIC CODES (most common among merchants)")
    print("=" * 80)
    print(f"{'Rank':<6} {'SIC Code':<12} {'Count':<8} {'%':<8} {'Description'}")
    print("-" * 80)
    for rank, (_, row) in enumerate(code_counts.head(top_n).iterrows(), 1):
        print(
            f"{rank:<6} {row['sic_code']:<12} {row['count']:<8} "
            f"{row['percentage']:<8.1f} {row['description']}"
        )
    print("=" * 80)

    # Coverage analysis: how many unique merchants are covered by different code sets?
    _coverage_analysis(df)

    # Save full frequency table
    out_path = "data/sic_frequency.csv"
    code_counts.to_csv(out_path, index=False)
    print(f"\nFull frequency table saved to {out_path}")

    return code_counts


def _coverage_analysis(df):
    """Show how many unique merchants are covered by top 3 vs all 12 plumbing codes."""
    sic_series = df["sic_codes"].fillna("").astype(str)
    total_with_sic = (sic_series.str.strip().ne("")).sum()

    top_3 = {"46740", "43220", "47520"}
    all_12 = {
        "46740", "43220", "47520", "46130", "46730", "47540",
        "25210", "33200", "43290", "35300", "36000", "37000",
    }

    def count_merchants_with_any(codes):
        count = 0
        for codes_str in sic_series:
            merchant_codes = {c.strip() for c in codes_str.split(",") if c.strip()}
            if merchant_codes & codes:
                count += 1
        return count

    top3_count = count_merchants_with_any(top_3)
    all12_count = count_merchants_with_any(all_12)
    lost = all12_count - top3_count

    print("\n" + "=" * 80)
    print("COVERAGE ANALYSIS: Top 3 vs All 12 plumbing codes")
    print("=" * 80)
    print(f"Total merchants with any SIC data:        {total_with_sic}")
    print(f"Merchants with top 3 codes:               {top3_count}  ({top3_count/total_with_sic*100:.1f}%)")
    print(f"  - 46740 (wholesale plumbing/heating)")
    print(f"  - 43220 (plumbing installation)")
    print(f"  - 47520 (retail hardware)")
    print(f"Merchants with any of 12 plumbing codes:  {all12_count}  ({all12_count/total_with_sic*100:.1f}%)")
    print(f"Merchants LOST by using only top 3:       {lost}  ({lost/total_with_sic*100:.1f}%)")
    print(f"Merchants with NO plumbing code at all:   {total_with_sic - all12_count}  ({(total_with_sic - all12_count)/total_with_sic*100:.1f}%)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Analyse SIC code frequency from output CSV")
    parser.add_argument(
        "-i", "--input", default="data/output.csv",
        help="Path to output CSV with sic_codes column (default: data/output.csv)"
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Number of top SIC codes to show (default: 20)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: File not found: {args.input}")
        print("Run the main pipeline first, or specify the correct path with -i")
        sys.exit(1)

    df = pd.read_csv(args.input)
    print(f"Loaded {len(df)} rows from {args.input}")
    analyse(df, top_n=args.top)


if __name__ == "__main__":
    main()
