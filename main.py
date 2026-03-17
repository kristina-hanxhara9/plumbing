import argparse
import logging
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

# Common SIC code descriptions (UK Companies House)
SIC_DESCRIPTIONS = {
    "43220": "Plumbing, heat and air-conditioning installation",
    "46740": "Wholesale of hardware, plumbing and heating equipment",
    "47523": "Retail sale of hardware, paints and glass",
    "47521": "Retail sale of hardware",
    "47520": "Retail sale of hardware, paints and glass",
    "47530": "Retail sale of carpets, rugs, wall and floor coverings",
    "43210": "Electrical installation",
    "43290": "Other construction installation",
    "43310": "Plastering",
    "43320": "Joinery installation",
    "43330": "Floor and wall covering",
    "43341": "Painting",
    "43342": "Glazing",
    "43390": "Other building completion and finishing",
    "43910": "Roofing activities",
    "43991": "Scaffold erection",
    "43999": "Other specialised construction activities n.e.c.",
    "43120": "Site preparation",
    "41100": "Development of building projects",
    "41201": "Construction of commercial buildings",
    "41202": "Construction of domestic buildings",
    "42110": "Construction of roads and motorways",
    "42910": "Construction of water projects",
    "42990": "Construction of other civil engineering projects",
    "46130": "Agents involved in sale of timber and building materials",
    "46730": "Wholesale of wood, construction materials and sanitary equipment",
    "46760": "Wholesale of other intermediate products",
    "46900": "Non-specialised wholesale trade",
    "47110": "Retail sale in non-specialised stores with food",
    "47190": "Other retail sale in non-specialised stores",
    "47540": "Retail sale of electrical household appliances",
    "47599": "Retail sale of furniture, lighting and household articles n.e.c.",
    "25210": "Manufacture of central heating radiators and boilers",
    "25290": "Manufacture of other tanks, reservoirs and containers of metal",
    "28220": "Manufacture of lifting and handling equipment",
    "33120": "Repair of machinery",
    "33200": "Installation of industrial machinery and equipment",
    "35300": "Steam and air conditioning supply",
    "36000": "Water collection, treatment and supply",
    "37000": "Sewerage",
    "38110": "Collection of non-hazardous waste",
    "45200": "Maintenance and repair of motor vehicles",
    "68100": "Buying and selling of own real estate",
    "68201": "Renting and operating of Housing Association real estate",
    "68209": "Other letting and operating of own or leased real estate",
    "68310": "Real estate agencies",
    "68320": "Management of real estate on a fee or contract basis",
    "70100": "Activities of head offices",
    "70221": "Financial management",
    "70229": "Management consultancy activities other than financial management",
    "81100": "Combined facilities support activities",
    "81210": "General cleaning of buildings",
    "81221": "Window cleaning services",
    "81222": "Specialised cleaning services",
    "81299": "Other cleaning activities",
    "82990": "Other business support service activities n.e.c.",
    "96090": "Other service activities n.e.c.",
}


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
    code_counts["description"] = code_counts["sic_code"].map(
        lambda x: SIC_DESCRIPTIONS.get(x, "")
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
