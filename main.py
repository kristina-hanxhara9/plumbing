import argparse
import logging
import os
import sys

import pandas as pd

from fetch_sic_codes import process_excel
from ml_model import (
    train_model,
    predict_sic_codes,
    format_predictions,
    save_model,
)
from config import DEFAULT_CONFIDENCE_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Path to the official UK SIC 2007 condensed reference (from Companies House / ONS)
SIC_REFERENCE_PATH = os.path.join(os.path.dirname(__file__), "data", "sic_reference.csv")


def _load_sic_descriptions():
    """Load SIC code descriptions from the official UK SIC 2007 reference file."""
    if not os.path.exists(SIC_REFERENCE_PATH):
        logger.warning(f"SIC reference file not found at {SIC_REFERENCE_PATH}")
        return {}
    ref = pd.read_csv(SIC_REFERENCE_PATH, dtype=str)
    return dict(zip(ref["sic_code"].str.strip(), ref["sic_description"].str.strip()))


def _analyse_sic_frequency(df):
    """Analyse which SIC codes appear most frequently in the API results.

    Splits comma-separated SIC codes, counts occurrences, and returns
    a ranked DataFrame with code, count, percentage, and description.
    """
    # Use actual API SIC codes, not predictions
    sic_series = df["sic_codes"].fillna("").astype(str)
    all_codes = []
    for codes_str in sic_series:
        for code in codes_str.split(","):
            code = code.strip()
            if code:
                all_codes.append(code)

    if not all_codes:
        return pd.DataFrame(columns=["sic_code", "count", "percentage", "description"])

    code_counts = pd.Series(all_codes).value_counts().reset_index()
    code_counts.columns = ["sic_code", "count"]
    total_companies = (sic_series.str.strip().ne("")).sum()
    code_counts["percentage"] = (code_counts["count"] / total_companies * 100).round(1)
    sic_descriptions = _load_sic_descriptions()
    code_counts["description"] = code_counts["sic_code"].map(
        lambda x: sic_descriptions.get(x, sic_descriptions.get(x.lstrip("0"), ""))
    )

    return code_counts


def main():
    parser = argparse.ArgumentParser(
        description="Fetch SIC codes from Companies House and predict SIC codes for plumbing merchants"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to input Excel file"
    )
    parser.add_argument(
        "-o", "--output", default="data/output.csv", help="Path to output CSV file"
    )
    parser.add_argument(
        "-m", "--model-path", default="model.pkl", help="Path to save/load model"
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip API fetching and use a previously enriched CSV (provide CSV as --input)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Confidence threshold for SIC code predictions (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    args = parser.parse_args()

    # Step 1: Get enriched data (fetch from API or load existing)
    if args.skip_fetch:
        logger.info(f"Loading previously enriched data from {args.input}")
        df = pd.read_csv(args.input)
    else:
        logger.info(f"Fetching SIC codes from Companies House API...")
        enriched_path = args.output.replace(".csv", "_enriched.csv")
        df = process_excel(args.input, enriched_path)

    # Step 2: Train ML model
    has_sic = df["sic_codes"].fillna("").astype(str).str.strip().ne("").sum()
    logger.info(f"Rows with SIC codes for training: {has_sic}/{len(df)}")

    if has_sic < 3:
        logger.error(
            "Not enough rows with SIC codes to train a model (need at least 3). "
            "Check your API key and that company names/postcodes are correct."
        )
        sys.exit(1)

    model, vectorizer, pc_vectorizer, binarizer = train_model(df)

    # Step 3: Predict SIC codes for ALL rows
    predictions = predict_sic_codes(
        model, df, vectorizer, pc_vectorizer, binarizer, threshold=args.threshold
    )
    df["predicted_sic_codes"] = format_predictions(predictions)

    # Step 4: Add confidence column (max confidence across predicted SIC codes)
    df["max_confidence"] = [
        max(pred.values()) if pred else 0.0 for pred in predictions
    ]

    # Step 5: Save output
    df.to_csv(args.output, index=False)
    logger.info(f"Output saved to {args.output}")

    # Step 6: Save model
    save_model(model, vectorizer, pc_vectorizer, binarizer, args.model_path)

    # Summary
    total = len(df)
    api_found = df["api_match_found"].sum() if "api_match_found" in df.columns else has_sic
    active = (df["company_status"] == "active").sum() if "company_status" in df.columns else 0
    has_predictions = (df["max_confidence"] > 0).sum()

    # Analyse SIC code frequency from actual API data
    sic_frequency = _analyse_sic_frequency(df)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total merchants:          {total}")
    print(f"API matches found:        {api_found}")
    print(f"Active companies:         {active}")
    print(f"With SIC predictions:     {has_predictions}")
    print(f"Unique SIC codes found:   {len(binarizer.classes_)}")
    print(f"Confidence threshold:     {args.threshold}")
    print(f"Output file:              {args.output}")
    print(f"Model saved to:           {args.model_path}")
    print("=" * 60)

    # Show top SIC codes by frequency
    print("\n" + "=" * 60)
    print("TOP SIC CODES (most common among plumbing merchants)")
    print("=" * 60)
    print(f"{'SIC Code':<12} {'Count':<8} {'% of merchants':<16} {'Description'}")
    print("-" * 60)
    for _, row in sic_frequency.head(20).iterrows():
        print(
            f"{row['sic_code']:<12} {row['count']:<8} "
            f"{row['percentage']:<16.1f} {row['description']}"
        )
    print("=" * 60)

    # Save SIC frequency analysis to CSV
    sic_freq_path = args.output.replace(".csv", "_sic_frequency.csv")
    sic_frequency.to_csv(sic_freq_path, index=False)
    logger.info(f"SIC code frequency analysis saved to {sic_freq_path}")


if __name__ == "__main__":
    main()
