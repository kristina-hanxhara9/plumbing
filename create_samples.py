"""Create 5 representative sample sheets of ~100 merchants each.

Each sheet maintains the same proportional breakdown as the full dataset:
  ~32%  merchants identifiable by plumbing SIC code (top 3)
  ~53%  merchants with plumbing keywords in name (but no plumbing SIC)
  ~15%  merchants with neither (unidentifiable)

Usage:
    python create_samples.py -i data/output.csv
    python create_samples.py -i data/output.csv -o data/samples.xlsx -n 5 -s 100
"""
import argparse
import os
import sys

import pandas as pd

from config import PLUMBING_SIC_CODES

# Top 3 plumbing SIC codes used for the SIC-identifiable bucket
TOP_3_SIC = {"46740", "43220", "47520"}

# Keywords to detect plumbing merchants by company name
PLUMBING_KEYWORDS = [
    "plumb", "heating", "bathroom", "boiler", "pipe", "radiator",
    "sanitary", "drain", "water", "hvac", "thermal", "gas",
    "central heating", "underfloor", "shower", "tap", "valve",
    "copper", "solder", "cistern", "flush", "waste", "soil",
    "merchant", "supply", "supplies", "wholesale", "trade",
    "builders", "hardware", "ironmong",
]


def has_plumbing_keyword(name):
    """Check if a company name contains any plumbing keyword."""
    name_lower = str(name).lower()
    return any(kw in name_lower for kw in PLUMBING_KEYWORDS)


def classify_merchants(df):
    """Split the dataframe into 3 buckets matching the analysis breakdown."""
    sic_series = df["sic_codes"].fillna("").astype(str)
    names = df.iloc[:, 0].fillna("").astype(str)

    bucket_sic = []      # has a top-3 plumbing SIC code
    bucket_keyword = []   # no plumbing SIC, but has plumbing keyword in name
    bucket_neither = []   # neither SIC nor keyword

    for i, codes_str in enumerate(sic_series):
        merchant_codes = {c.strip() for c in codes_str.split(",") if c.strip()}
        if merchant_codes & TOP_3_SIC:
            bucket_sic.append(i)
        elif has_plumbing_keyword(names.iloc[i]):
            bucket_keyword.append(i)
        else:
            bucket_neither.append(i)

    return bucket_sic, bucket_keyword, bucket_neither


def create_samples(df, n_samples=5, sample_size=100, seed=42):
    """Create n_samples representative samples of sample_size each."""
    bucket_sic, bucket_keyword, bucket_neither = classify_merchants(df)

    total = len(bucket_sic) + len(bucket_keyword) + len(bucket_neither)
    pct_sic = len(bucket_sic) / total
    pct_kw = len(bucket_keyword) / total
    pct_neither = len(bucket_neither) / total

    # Calculate how many from each bucket per sample
    n_sic = round(sample_size * pct_sic)
    n_kw = round(sample_size * pct_kw)
    n_neither = sample_size - n_sic - n_kw  # remainder to hit exact sample_size

    print(f"Full dataset: {total} merchants")
    print(f"  Bucket 1 – Plumbing SIC code:     {len(bucket_sic)} ({pct_sic*100:.1f}%)")
    print(f"  Bucket 2 – Keyword match only:     {len(bucket_keyword)} ({pct_kw*100:.1f}%)")
    print(f"  Bucket 3 – Neither (unidentifiable):{len(bucket_neither)} ({pct_neither*100:.1f}%)")
    print()
    print(f"Each sample of {sample_size}: {n_sic} SIC + {n_kw} keyword + {n_neither} neither")
    print()

    import numpy as np
    rng = np.random.default_rng(seed)

    # Shuffle each bucket once
    sic_idx = rng.permutation(bucket_sic)
    kw_idx = rng.permutation(bucket_keyword)
    neither_idx = rng.permutation(bucket_neither)

    samples = []
    for i in range(n_samples):
        # Pick non-overlapping slices where possible; wrap around if needed
        def pick(pool, n, offset):
            start = offset * n
            if start + n <= len(pool):
                return pool[start:start + n]
            # Wrap around / reshuffle if we run out
            picked = list(pool[start:])
            remaining = n - len(picked)
            picked.extend(rng.choice(pool, size=remaining, replace=False))
            return picked

        chosen_sic = pick(sic_idx, n_sic, i)
        chosen_kw = pick(kw_idx, n_kw, i)
        chosen_neither = pick(neither_idx, n_neither, i)

        all_chosen = list(chosen_sic) + list(chosen_kw) + list(chosen_neither)
        sample_df = df.iloc[all_chosen].copy()

        # Add a column indicating how this merchant was classified
        labels = (
            ["SIC code match"] * len(chosen_sic)
            + ["Keyword match"] * len(chosen_kw)
            + ["Neither"] * len(chosen_neither)
        )
        sample_df.insert(0, "classification", labels)
        samples.append(sample_df)

        print(f"  Sample {i+1}: {len(chosen_sic)} SIC + {len(chosen_kw)} keyword + {len(chosen_neither)} neither = {len(sample_df)}")

    return samples


def main():
    parser = argparse.ArgumentParser(
        description="Create representative sample sheets from enriched merchant data"
    )
    parser.add_argument(
        "-i", "--input", default="data/output.csv",
        help="Path to enriched CSV with sic_codes column (default: data/output.csv)",
    )
    parser.add_argument(
        "-o", "--output", default="data/samples.xlsx",
        help="Path to output Excel file (default: data/samples.xlsx)",
    )
    parser.add_argument(
        "-n", "--num-samples", type=int, default=5,
        help="Number of sample sheets to create (default: 5)",
    )
    parser.add_argument(
        "-s", "--sample-size", type=int, default=100,
        help="Number of merchants per sample (default: 100)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: File not found: {args.input}")
        print("Run the main pipeline first, or specify the correct path with -i")
        sys.exit(1)

    df = pd.read_csv(args.input)
    print(f"Loaded {len(df)} rows from {args.input}\n")

    samples = create_samples(
        df,
        n_samples=args.num_samples,
        sample_size=args.sample_size,
        seed=args.seed,
    )

    # Write to Excel with one sheet per sample
    with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
        for i, sample_df in enumerate(samples, 1):
            sheet_name = f"Sample {i}"
            sample_df.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\nSaved {len(samples)} sample sheets to {args.output}")


if __name__ == "__main__":
    main()
