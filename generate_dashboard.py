"""Generate sample plumbing merchant data and an interactive HTML chart dashboard.

Creates 100 realistic UK plumbing merchants, then produces a beautiful
interactive dashboard with 8 charts analysing SIC codes, keywords,
company types, geography, and ML confidence scores.

Usage:
    python generate_dashboard.py                    # Generate data + charts
    python generate_dashboard.py --skip-generate    # Charts only from existing CSV
    python generate_dashboard.py --html data/my_dashboard.html
"""
import argparse
import os
import random
import string

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

from config import PLUMBING_SIC_CODES

# ---------------------------------------------------------------------------
# Constants (mirrored from create_samples.py)
# ---------------------------------------------------------------------------
TOP_3_SIC = {"46740", "43220", "47520"}

PLUMBING_KEYWORDS = [
    "plumb", "heating", "bathroom", "boiler", "pipe", "radiator",
    "sanitary", "drain", "water", "hvac", "thermal", "gas",
    "central heating", "underfloor", "shower", "tap", "valve",
    "copper", "solder", "cistern", "flush", "waste", "soil",
    "merchant", "supply", "supplies", "wholesale", "trade",
    "builders", "hardware", "ironmong",
]

# ---------------------------------------------------------------------------
# Name building blocks
# ---------------------------------------------------------------------------
SURNAMES = [
    "Williams", "Jones", "Smith", "Taylor", "Brown", "Wilson", "Davies",
    "Evans", "Thomas", "Roberts", "Johnson", "Walker", "Wright", "Thompson",
    "Robinson", "Hall", "Clarke", "Green", "King", "Baker", "Harris",
    "Turner", "Collins", "Morgan", "Murray", "Stewart", "Campbell", "Reid",
    "Mitchell", "Douglas",
]

TOWNS = [
    "Bristol", "Leeds", "Manchester", "Birmingham", "Sheffield", "Liverpool",
    "Nottingham", "Leicester", "Cardiff", "Glasgow", "Edinburgh", "Plymouth",
    "Southampton", "Brighton", "Exeter", "Norwich", "York", "Chester",
    "Derby", "Wolverhampton", "Sunderland", "Stockport", "Bolton",
    "Blackburn", "Crawley", "Ipswich", "Swansea", "Newport", "Dundee",
    "Aberdeen",
]

# Descriptors that contain plumbing keywords
PLUMBING_DESCRIPTORS = [
    "Plumbing Supplies", "Heating & Plumbing", "Bathroom Centre",
    "Plumbers Merchant", "Pipe & Fittings", "Boiler Services",
    "Radiator Warehouse", "Plumbing & Heating Wholesale",
    "Bathroom Supplies", "Heating Supplies", "Plumbing Trade Supplies",
    "Drainage Solutions", "Water Systems", "Gas & Heating",
    "Plumbing Merchants", "Sanitary Ware", "Thermal Solutions",
    "Valve & Fittings", "Shower & Bathroom", "Tap & Shower",
]

# Descriptors without plumbing keywords
GENERIC_DESCRIPTORS = [
    "Building Supplies", "Construction Services", "Industrial Components",
    "Home Improvement Centre", "Property Services", "Maintenance Group",
    "Technical Services", "Engineering Solutions", "Commercial Services",
    "Professional Services", "Site Services", "Infrastructure Ltd",
    "Development Co", "Projects Group", "Facilities Management",
]

# UK postcode areas with realistic districts
POSTCODE_AREAS = [
    ("BS", 1, 16), ("LS", 1, 27), ("M", 1, 46), ("B", 1, 38),
    ("S", 1, 43), ("L", 1, 40), ("NG", 1, 25), ("LE", 1, 19),
    ("CF", 1, 15), ("G", 1, 34), ("EH", 1, 17), ("PL", 1, 9),
    ("SO", 14, 23), ("BN", 1, 18), ("EX", 1, 8), ("NR", 1, 13),
    ("YO", 1, 8), ("CH", 1, 8), ("DE", 1, 15), ("WV", 1, 11),
    ("SW", 1, 20), ("SE", 1, 28), ("E", 1, 18), ("N", 1, 22),
    ("W", 1, 14), ("EC", 1, 4), ("WC", 1, 2), ("NW", 1, 11),
]

# SIC codes for non-plumbing companies
NON_PLUMBING_SIC = [
    "47110", "47190", "47599", "68100", "82990", "96090",
    "41100", "43390", "43999", "45200", "47789", "49410",
]


def _random_postcode(rng):
    area, lo, hi = rng.choice(POSTCODE_AREAS)
    district = rng.randint(lo, hi)
    sector = rng.randint(0, 9)
    unit = "".join(rng.choices(string.ascii_uppercase, k=2))
    return f"{area}{district} {sector}{unit}"


def _random_company_number(rng):
    return f"{rng.randint(1_000_000, 99_999_999):08d}"


def _weighted_choice(rng, options, weights):
    return rng.choices(options, weights=weights, k=1)[0]


def _make_name(rng, descriptors):
    """Build a realistic company name from components."""
    pattern = rng.choice(["surname", "town", "double", "acronym"])
    if pattern == "surname":
        name = f"{rng.choice(SURNAMES)} {rng.choice(descriptors)} Ltd"
    elif pattern == "town":
        name = f"{rng.choice(TOWNS)} {rng.choice(descriptors)} Ltd"
    elif pattern == "double":
        s1, s2 = rng.sample(SURNAMES, 2)
        name = f"{s1} & {s2} {rng.choice(descriptors)} Ltd"
    else:
        letters = "".join(rng.choices(string.ascii_uppercase, k=3))
        name = f"{letters} {rng.choice(descriptors)} Ltd"
    return name


# ---------------------------------------------------------------------------
# Sample data generation
# ---------------------------------------------------------------------------

def generate_sample_data(sic_ref_path, output_path, n=100, seed=42):
    """Generate n realistic plumbing merchant records."""
    rng = random.Random(seed)

    # Proportions matching create_samples.py documentation
    n_sic = round(n * 0.32)       # 32 SIC-identifiable
    n_kw = round(n * 0.53)        # 53 keyword-match
    n_neither = n - n_sic - n_kw  # 15 neither

    rows = []
    used_names = set()

    def _unique_name(rng, descriptors):
        for _ in range(50):
            name = _make_name(rng, descriptors)
            if name not in used_names:
                used_names.add(name)
                return name
        return _make_name(rng, descriptors) + f" ({rng.randint(1, 999)})"

    # --- Bucket 1: SIC code match (has top-3 plumbing SIC) ---
    for _ in range(n_sic):
        primary = _weighted_choice(rng, ["46740", "43220", "47520"], [60, 25, 15])
        codes = [primary]
        if rng.random() < 0.3:
            extra = rng.choice(list(PLUMBING_SIC_CODES - {primary}))
            codes.append(extra)
        rows.append({
            "company_name": _unique_name(rng, PLUMBING_DESCRIPTORS),
            "company_number": _random_company_number(rng),
            "company_status": _weighted_choice(
                rng, ["active", "dissolved", "liquidation", "administration"],
                [85, 10, 3, 2]),
            "company_type": _weighted_choice(
                rng, ["ltd", "private-limited-guarant-nsc", "llp", "plc", "other"],
                [75, 10, 8, 5, 2]),
            "postcode": _random_postcode(rng),
            "sic_codes": ",".join(codes),
            "predicted_sic_codes": ",".join(codes),
            "max_confidence": round(rng.uniform(0.70, 0.95), 3),
        })

    # --- Bucket 2: Keyword match (plumbing keyword in name, no plumbing SIC) ---
    for _ in range(n_kw):
        codes = [rng.choice(NON_PLUMBING_SIC)]
        if rng.random() < 0.2:
            codes.append(rng.choice(NON_PLUMBING_SIC))
        predicted = rng.choice(["46740", "43220", "47520"])
        rows.append({
            "company_name": _unique_name(rng, PLUMBING_DESCRIPTORS),
            "company_number": _random_company_number(rng),
            "company_status": _weighted_choice(
                rng, ["active", "dissolved", "liquidation", "administration"],
                [85, 10, 3, 2]),
            "company_type": _weighted_choice(
                rng, ["ltd", "private-limited-guarant-nsc", "llp", "plc", "other"],
                [75, 10, 8, 5, 2]),
            "postcode": _random_postcode(rng),
            "sic_codes": ",".join(codes),
            "predicted_sic_codes": predicted,
            "max_confidence": round(rng.uniform(0.30, 0.70), 3),
        })

    # --- Bucket 3: Neither (no plumbing SIC, no keyword) ---
    for _ in range(n_neither):
        codes = [rng.choice(NON_PLUMBING_SIC)]
        rows.append({
            "company_name": _unique_name(rng, GENERIC_DESCRIPTORS),
            "company_number": _random_company_number(rng),
            "company_status": _weighted_choice(
                rng, ["active", "dissolved", "liquidation", "administration"],
                [85, 10, 3, 2]),
            "company_type": _weighted_choice(
                rng, ["ltd", "private-limited-guarant-nsc", "llp", "plc", "other"],
                [75, 10, 8, 5, 2]),
            "postcode": _random_postcode(rng),
            "sic_codes": ",".join(codes),
            "predicted_sic_codes": "",
            "max_confidence": round(rng.uniform(0.00, 0.30), 3),
        })

    df = pd.DataFrame(rows)
    rng.shuffle(rows)  # Mix the buckets
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"Generated {len(df)} sample merchants -> {output_path}")
    return df


# ---------------------------------------------------------------------------
# Classification (mirrors create_samples.py)
# ---------------------------------------------------------------------------

def classify_row(row):
    codes = {c.strip() for c in str(row["sic_codes"]).split(",") if c.strip()}
    if codes & TOP_3_SIC:
        return "SIC Code Match"
    name_lower = str(row["company_name"]).lower()
    if any(kw in name_lower for kw in PLUMBING_KEYWORDS):
        return "Keyword Match"
    return "Neither"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse_data(df, sic_ref):
    """Compute all chart-ready data structures."""
    results = {}

    # --- 1. SIC code distribution ---
    all_codes = (
        df["sic_codes"].dropna().astype(str)
        .str.split(",").explode().str.strip()
    )
    all_codes = all_codes[all_codes != ""]
    sic_counts = all_codes.value_counts().reset_index()
    sic_counts.columns = ["sic_code", "count"]

    # Join descriptions
    sic_ref_clean = sic_ref[["sic_code", "sic_description"]].copy()
    sic_ref_clean["sic_code"] = sic_ref_clean["sic_code"].astype(str).str.strip()
    sic_counts = sic_counts.merge(sic_ref_clean, on="sic_code", how="left")
    sic_counts["sic_description"] = sic_counts["sic_description"].fillna("Unknown")
    sic_counts["label"] = sic_counts["sic_code"] + " - " + sic_counts["sic_description"]
    results["sic_dist"] = sic_counts

    # --- 2. Top SIC for pie ---
    top6 = sic_counts.head(6).copy()
    other_count = sic_counts.iloc[6:]["count"].sum() if len(sic_counts) > 6 else 0
    if other_count > 0:
        other_row = pd.DataFrame([{"label": "Other", "count": other_count}])
        top6 = pd.concat([top6, other_row], ignore_index=True)
    results["sic_pie"] = top6

    # --- 3. Classification ---
    df["classification"] = df.apply(classify_row, axis=1)
    class_counts = df["classification"].value_counts().reset_index()
    class_counts.columns = ["classification", "count"]
    results["classification"] = class_counts

    # --- 4. Keyword frequency ---
    kw_freq = {}
    names_lower = df["company_name"].str.lower()
    for kw in PLUMBING_KEYWORDS:
        kw_freq[kw] = names_lower.str.contains(kw, na=False).sum()
    kw_df = (
        pd.DataFrame(list(kw_freq.items()), columns=["keyword", "count"])
        .sort_values("count", ascending=False)
        .head(15)
    )
    results["keywords"] = kw_df

    # --- 5. Company type ---
    type_counts = df["company_type"].value_counts().reset_index()
    type_counts.columns = ["company_type", "count"]
    results["company_type"] = type_counts

    # --- 6. Company status ---
    status_counts = df["company_status"].value_counts().reset_index()
    status_counts.columns = ["company_status", "count"]
    results["company_status"] = status_counts

    # --- 7. Geographic distribution ---
    df["postcode_area"] = df["postcode"].str.extract(r"^([A-Z]{1,2})", expand=False)
    geo_counts = df["postcode_area"].value_counts().head(15).reset_index()
    geo_counts.columns = ["area", "count"]
    results["geography"] = geo_counts

    # --- 8. Confidence distribution ---
    results["confidence"] = df["max_confidence"]

    # --- Summary stats ---
    results["total"] = len(df)
    results["active_pct"] = round(
        (df["company_status"] == "active").mean() * 100, 1
    )
    results["top_sic"] = sic_counts.iloc[0]["label"] if len(sic_counts) > 0 else "N/A"
    results["avg_confidence"] = round(df["max_confidence"].mean(), 3)

    return results


# ---------------------------------------------------------------------------
# Dashboard generation
# ---------------------------------------------------------------------------

COLORS = {
    "SIC Code Match": "#2ecc71",
    "Keyword Match": "#3498db",
    "Neither": "#95a5a6",
}

STATUS_COLORS = {
    "active": "#2ecc71",
    "dissolved": "#e74c3c",
    "liquidation": "#e67e22",
    "administration": "#f39c12",
}


def generate_dashboard(analysis, output_path):
    """Build a single-page HTML dashboard with 8 plotly charts."""

    # 1. SIC Code Distribution (horizontal bar)
    sic = analysis["sic_dist"].sort_values("count", ascending=True)
    fig1 = px.bar(
        sic, x="count", y="label", orientation="h",
        title="SIC Code Distribution Across Plumbing Merchants",
        labels={"count": "Number of Companies", "label": "SIC Code"},
        color="count", color_continuous_scale="Blues",
    )
    fig1.update_layout(height=max(400, len(sic) * 35), showlegend=False)

    # 2. Top SIC Codes pie
    pie_data = analysis["sic_pie"]
    fig2 = px.pie(
        pie_data, values="count", names="label",
        title="Top SIC Code Share",
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig2.update_traces(textposition="inside", textinfo="percent+label")
    fig2.update_layout(height=450)

    # 3. Classification donut
    cls = analysis["classification"]
    fig3 = go.Figure(go.Pie(
        labels=cls["classification"], values=cls["count"],
        hole=0.45,
        marker=dict(colors=[COLORS.get(c, "#bdc3c7") for c in cls["classification"]]),
    ))
    fig3.update_layout(title="How Merchants Were Classified", height=450)

    # 4. Keyword frequency (horizontal bar)
    kw = analysis["keywords"]
    fig4 = px.bar(
        kw, x="count", y="keyword", orientation="h",
        title="Plumbing Keywords Found in Company Names (Top 15)",
        labels={"count": "Occurrences", "keyword": "Keyword"},
        color="count", color_continuous_scale="Teal",
    )
    fig4.update_layout(height=500, yaxis=dict(autorange="reversed"), showlegend=False)

    # 5. Company type pie
    ct = analysis["company_type"]
    fig5 = px.pie(
        ct, values="count", names="company_type",
        title="Company Type Distribution",
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    fig5.update_layout(height=400)

    # 6. Company status bar
    cs = analysis["company_status"]
    fig6 = px.bar(
        cs, x="company_status", y="count",
        title="Company Status",
        labels={"company_status": "Status", "count": "Count"},
        color="company_status",
        color_discrete_map=STATUS_COLORS,
    )
    fig6.update_layout(height=400, showlegend=False)

    # 7. Geographic distribution
    geo = analysis["geography"]
    fig7 = px.bar(
        geo, x="area", y="count",
        title="Geographic Distribution by Postcode Area (Top 15)",
        labels={"area": "Postcode Area", "count": "Count"},
        color="count", color_continuous_scale="Viridis",
    )
    fig7.update_layout(height=400, showlegend=False)

    # 8. Confidence histogram
    fig8 = px.histogram(
        x=analysis["confidence"], nbins=10,
        title="ML Confidence Score Distribution",
        labels={"x": "Confidence Score", "y": "Count"},
        color_discrete_sequence=["#8e44ad"],
    )
    fig8.update_layout(height=400, bargap=0.05)

    # Build HTML
    figs = [fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8]
    chart_divs = []
    for fig in figs:
        fig.update_layout(
            template="plotly_white",
            font=dict(family="Segoe UI, Helvetica, Arial, sans-serif"),
            margin=dict(l=20, r=20, t=50, b=20),
        )
        div = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        chart_divs.append(div)

    plotly_js = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'

    stats = f"""
    <div class="stats-bar">
        <div class="stat-card">
            <div class="stat-value">{analysis['total']}</div>
            <div class="stat-label">Total Companies</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{analysis['active_pct']}%</div>
            <div class="stat-label">Active</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{analysis['avg_confidence']}</div>
            <div class="stat-label">Avg Confidence</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="font-size:0.9em">{analysis['top_sic'][:30]}</div>
            <div class="stat-label">Most Common SIC</div>
        </div>
    </div>
    """

    cards_html = ""
    for i, div in enumerate(chart_divs):
        span = "span-2" if i in (0, 3) else ""  # Wide charts for bars
        cards_html += f'<div class="chart-card {span}">{div}</div>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Plumbing Merchants Analysis Dashboard</title>
{plotly_js}
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Helvetica, Arial, sans-serif; background: #f0f2f5; color: #333; }}
.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: white; padding: 2rem; text-align: center;
}}
.header h1 {{ font-size: 2rem; font-weight: 300; letter-spacing: 1px; }}
.header p {{ opacity: 0.7; margin-top: 0.5rem; }}
.stats-bar {{
    display: flex; justify-content: center; gap: 1.5rem;
    padding: 1.5rem; flex-wrap: wrap;
}}
.stat-card {{
    background: white; border-radius: 12px; padding: 1.2rem 2rem;
    text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    min-width: 160px;
}}
.stat-value {{ font-size: 1.6rem; font-weight: 700; color: #0f3460; }}
.stat-label {{ font-size: 0.8rem; color: #888; margin-top: 0.3rem; text-transform: uppercase; }}
.grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1.5rem;
    padding: 0 1.5rem 2rem;
    max-width: 1400px;
    margin: 0 auto;
}}
.chart-card {{
    background: white; border-radius: 12px; padding: 1rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    overflow: hidden;
}}
.chart-card.span-2 {{ grid-column: span 2; }}
@media (max-width: 900px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .chart-card.span-2 {{ grid-column: span 1; }}
}}
</style>
</head>
<body>
<div class="header">
    <h1>Plumbing Merchants Analysis Dashboard</h1>
    <p>Interactive analysis of {analysis['total']} sample UK plumbing merchants</p>
</div>
{stats}
<div class="grid">
{cards_html}
</div>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
    print(f"Dashboard saved -> {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate sample plumbing merchant data and interactive charts"
    )
    parser.add_argument(
        "-o", "--output-csv", default="data/output.csv",
        help="Path for generated CSV (default: data/output.csv)",
    )
    parser.add_argument(
        "--html", default="data/sample_charts.html",
        help="Path for HTML dashboard (default: data/sample_charts.html)",
    )
    parser.add_argument(
        "--sic-ref", default="data/sic_reference.csv",
        help="Path to SIC reference CSV (default: data/sic_reference.csv)",
    )
    parser.add_argument(
        "--skip-generate", action="store_true",
        help="Skip data generation, use existing CSV",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    if not args.skip_generate:
        generate_sample_data(args.sic_ref, args.output_csv, seed=args.seed)

    if not os.path.exists(args.output_csv):
        print(f"ERROR: {args.output_csv} not found. Run without --skip-generate first.")
        return

    df = pd.read_csv(args.output_csv)
    sic_ref = pd.read_csv(args.sic_ref, dtype=str)
    print(f"Loaded {len(df)} merchants from {args.output_csv}")

    analysis = analyse_data(df, sic_ref)
    generate_dashboard(analysis, args.html)


if __name__ == "__main__":
    main()
