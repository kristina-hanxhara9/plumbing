import re
import logging

import numpy as np
import pandas as pd
import joblib
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import MultiLabelBinarizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

from config import DEFAULT_CONFIDENCE_THRESHOLD, PLUMBING_SIC_CODES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _extract_postcode_area(postcode):
    """Extract the area prefix from a UK postcode (e.g., 'SW1A 1AA' -> 'SW')."""
    if not postcode or not isinstance(postcode, str):
        return "UNKNOWN"
    match = re.match(r"^([A-Z]{1,2})", postcode.strip().upper())
    return match.group(1) if match else "UNKNOWN"


def prepare_features(df, name_col=0, postcode_col=4, fit=True,
                     vectorizer=None, postcode_vectorizer=None):
    """Build feature matrix from company names and postcodes.

    Args:
        df: DataFrame with company data.
        name_col: Column index for company name.
        postcode_col: Column index for postcode.
        fit: If True, fit new vectorizers. If False, use provided ones.
        vectorizer: Pre-fitted TfidfVectorizer for names (used when fit=False).
        postcode_vectorizer: Pre-fitted TfidfVectorizer for postcodes (used when fit=False).

    Returns:
        X: sparse feature matrix
        vectorizer: fitted TfidfVectorizer for names
        postcode_vectorizer: fitted TfidfVectorizer for postcodes
    """
    names = df.iloc[:, name_col].fillna("").astype(str)
    postcodes = df.iloc[:, postcode_col].fillna("").astype(str)
    postcode_areas = postcodes.apply(_extract_postcode_area)

    if fit:
        vectorizer = TfidfVectorizer(
            max_features=500, ngram_range=(1, 2), lowercase=True, stop_words="english"
        )
        name_features = vectorizer.fit_transform(names)

        postcode_vectorizer = TfidfVectorizer(max_features=100, analyzer="word")
        pc_features = postcode_vectorizer.fit_transform(postcode_areas)
    else:
        name_features = vectorizer.transform(names)
        pc_features = postcode_vectorizer.transform(postcode_areas)

    X = hstack([name_features, pc_features])
    return X, vectorizer, postcode_vectorizer


def prepare_labels(df, sic_col="sic_codes", fit=True, binarizer=None):
    """Build multi-label binary matrix from SIC codes.

    Only keeps plumbing-relevant SIC codes (defined in config.PLUMBING_SIC_CODES).
    All other codes are filtered out to reduce noise and improve model accuracy.

    Args:
        df: DataFrame with sic_codes column (comma-separated strings).
        sic_col: Name of the SIC codes column.
        fit: If True, fit new binarizer. If False, use provided one.
        binarizer: Pre-fitted MultiLabelBinarizer (used when fit=False).

    Returns:
        Y: binary label matrix
        binarizer: fitted MultiLabelBinarizer
    """
    sic_lists = (
        df[sic_col]
        .fillna("")
        .astype(str)
        .apply(lambda x: [s.strip() for s in x.split(",")
                          if s.strip() and s.strip() in PLUMBING_SIC_CODES])
    )

    if fit:
        binarizer = MultiLabelBinarizer()
        Y = binarizer.fit_transform(sic_lists)
    else:
        Y = binarizer.transform(sic_lists)

    return Y, binarizer


def train_model(df):
    """Train the multi-label SIC code predictor.

    Args:
        df: Enriched DataFrame with API results. Must have sic_codes column.

    Returns:
        model: trained OneVsRestClassifier
        vectorizer: fitted TfidfVectorizer for names
        postcode_vectorizer: fitted TfidfVectorizer for postcodes
        binarizer: fitted MultiLabelBinarizer
    """
    # Filter to rows where we have SIC codes from the API
    train_df = df[df["sic_codes"].fillna("").astype(str).str.strip().ne("")].copy()
    logger.info(f"Rows with any SIC codes: {len(train_df)}")

    # Filter labels to plumbing-relevant codes only
    X, vectorizer, postcode_vectorizer = prepare_features(train_df, fit=True)
    Y, binarizer = prepare_labels(train_df, fit=True)

    # Drop rows that have no plumbing-relevant SIC codes (all-zero label rows)
    has_plumbing_label = Y.sum(axis=1) > 0
    plumbing_count = has_plumbing_label.sum()
    logger.info(
        f"Rows with plumbing-relevant SIC codes: {plumbing_count}/{len(train_df)} "
        f"(filtered from {263} total unique codes to {len(binarizer.classes_)} plumbing codes)"
    )
    logger.info(f"Plumbing SIC codes used: {list(binarizer.classes_)}")

    if plumbing_count < 5:
        logger.warning("Too few training samples with plumbing codes — model will be unreliable")

    logger.info(f"Feature matrix: {X.shape}, Label matrix: {Y.shape}")

    # Train/test split if enough data
    if len(train_df) >= 20:
        X_train, X_test, Y_train, Y_test = train_test_split(
            X, Y, test_size=0.2, random_state=42
        )
    else:
        X_train, X_test, Y_train, Y_test = X, X, Y, Y
        logger.warning("Dataset too small for proper train/test split — using all data")

    # Train model
    model = OneVsRestClassifier(
        LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    )
    model.fit(X_train, Y_train)

    # Evaluate
    Y_pred = model.predict(X_test)
    logger.info("Classification report on test set:")
    report = classification_report(
        Y_test, Y_pred, target_names=binarizer.classes_, zero_division=0
    )
    logger.info(f"\n{report}")

    return model, vectorizer, postcode_vectorizer, binarizer


def predict_sic_codes(model, df, vectorizer, postcode_vectorizer, binarizer,
                      threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Predict SIC codes with confidence for all rows.

    Returns:
        List of dicts, one per row: {sic_code: confidence, ...}
    """
    X, _, _ = prepare_features(
        df, fit=False, vectorizer=vectorizer, postcode_vectorizer=postcode_vectorizer
    )

    # Get probability estimates
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
    else:
        # Fallback for models without predict_proba
        proba = model.decision_function(X)
        # Normalise to 0-1 range
        proba = 1 / (1 + np.exp(-proba))

    results = []
    for i in range(proba.shape[0]):
        row_predictions = {}
        for j, sic_code in enumerate(binarizer.classes_):
            conf = float(proba[i, j])
            if conf >= threshold:
                row_predictions[sic_code] = round(conf, 3)
        # Sort by confidence descending
        row_predictions = dict(
            sorted(row_predictions.items(), key=lambda x: x[1], reverse=True)
        )
        results.append(row_predictions)

    return results


def format_predictions(predictions):
    """Format predictions as readable strings.

    Returns list of strings like '46740 (0.85), 43220 (0.62)'
    """
    formatted = []
    for pred in predictions:
        parts = [f"{code} ({conf:.2f})" for code, conf in pred.items()]
        formatted.append(", ".join(parts) if parts else "No predictions above threshold")
    return formatted


def save_model(model, vectorizer, postcode_vectorizer, binarizer, path="model.pkl"):
    """Save all model components to a single file."""
    bundle = {
        "model": model,
        "vectorizer": vectorizer,
        "postcode_vectorizer": postcode_vectorizer,
        "binarizer": binarizer,
    }
    joblib.dump(bundle, path)
    logger.info(f"Model saved to {path}")


def load_model(path="model.pkl"):
    """Load model components from file."""
    bundle = joblib.load(path)
    return (
        bundle["model"],
        bundle["vectorizer"],
        bundle["postcode_vectorizer"],
        bundle["binarizer"],
    )
