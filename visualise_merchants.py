"""Interactive HTML dashboard for 100 sample plumbing merchants.

Generates beautiful Plotly charts covering:
  - SIC code distribution (bar + pie)
  - Keyword analysis (which plumbing terms appear in company names)
  - Geographic distribution by UK region
  - Company size by accounts type
  - Confidence score distribution
  - Chain vs independent breakdown
  - Company age histogram
  - Accounts filing status

Usage:
    python visualise_merchants.py
    python visualise_merchants.py -o reports/dashboard.html
    python visualise_merchants.py -i data/my_merchants.csv   # use your own data
"""
import argparse
import os
import re
from datetime import datetime, timedelta
import random

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# ──────────────────────────────────────────────────────────────────────
# 100 SAMPLE PLUMBING MERCHANTS
# ──────────────────────────────────────────────────────────────────────
random.seed(42)

_NAMES = [
    # Core plumbing wholesalers (SIC 46740)
    "CITY PLUMBING SUPPLIES HOLDINGS LTD", "WOLSELEY UK LTD", "PLUMB CENTER LTD",
    "GRAHAM PLUMBERS MERCHANT LTD", "PLUMBASE LTD", "JOHNS PLUMBING SUPPLIES LTD",
    "NORTHERN PLUMBING SUPPLIES LTD", "MILES PLUMBING SUPPLIES LTD",
    "TOTAL PLUMBING SUPPLIES LTD", "NATIONWIDE PLUMBING SUPPLIES LTD",
    "SOUTH WEST PLUMBING SUPPLIES LTD", "MIDLAND PLUMBING SUPPLIES LTD",
    "ANGLO PLUMBING SUPPLIES LTD", "CROWN PLUMBING SUPPLIES LTD",
    "ATLANTIC PLUMBING SUPPLIES LTD", "JAMES HARGREAVES PLUMBING DEPOT LTD",
    "KELLAWAY BUILDING SUPPLIES LTD", "NEW QUAY PLUMBING SUPPLIES LTD",
    "ROBINSON QUAY PLUMBING LTD", "PIPESTOCK LTD",
    "GS KELLY PLUMBING MERCHANTS LTD", "BEGGS AND PARTNERS LTD",
    "HALDANE FISHER LTD", "J & A YOUNG PLUMBING LTD",
    "DAVIS & BOWRING PLUMBING SUPPLIES LTD", "KINGS PLUMBING SUPPLIES LTD",
    "ATLAS PLUMBING & HEATING SUPPLIES LTD", "DELTA PLUMBING SUPPLIES LTD",
    "PHOENIX PLUMBING MERCHANTS LTD", "EAGLE PLUMBING SUPPLIES LTD",
    "PREMIER PLUMBING SUPPLIES LTD", "ELITE PLUMBING MERCHANTS LTD",
    # Heating specialists (SIC 46740 + 43220)
    "BRITISH GAS HEATING SOLUTIONS LTD", "VAILLANT GROUP UK LTD",
    "BAXI HEATING UK LTD", "IDEAL HEATING LTD", "WORCESTER BOSCH GROUP LTD",
    "GLOW WORM HEATING LTD", "POTTERTON HEATING LTD",
    "STELRAD RADIATOR GROUP LTD", "DIMPLEX UK LTD",
    "CENTRAL HEATING SUPPLIES LTD", "SOUTHERN HEATING SUPPLIES LTD",
    "NORTH WEST HEATING SUPPLIES LTD", "RADIANT HEATING SOLUTIONS LTD",
    # Bathroom specialists (SIC 46740 + 47520)
    "VICTORIA PLUMB LTD", "BATHSTORE LTD", "IDEAL BATHROOMS LTD",
    "SANCTUARY BATHROOMS LTD", "CROSSWATER LTD",
    "HERITAGE BATHROOMS LTD", "MERLYN SHOWERING LTD",
    "MATKI PLC", "ROMAN SHOWERS LTD", "JACUZZI UK GROUP LTD",
    # Plumbing installation (SIC 43220)
    "PIMLICO PLUMBERS LTD", "DYNO-ROD LTD", "BRITISH GAS SERVICES LTD",
    "HOMESERVE PLUMBING & DRAINS LTD", "ASPECT PLUMBING & HEATING LTD",
    "WARMAWAY HEATING SYSTEMS LTD", "AQUAFLOW PLUMBING SERVICES LTD",
    "DRAINFAST LTD", "WASTEWATER SOLUTIONS LTD",
    # Hardware retail (SIC 47520)
    "SCREWFIX DIRECT LTD", "TOOLSTATION LTD", "WICKES BUILDING SUPPLIES LTD",
    "SELCO BUILDERS WAREHOUSE LTD", "BUILDBASE LTD",
    "TRAVIS PERKINS TRADING CO LTD", "JEWSON LTD",
    "MKM BUILDING SUPPLIES LTD", "HUWS GRAY LTD",
    # Mixed / related trades
    "FERNOX LTD", "POLYPIPE GROUP LTD", "WAVIN LTD",
    "HEPWORTH BUILDING PRODUCTS LTD", "MARLEY PLUMBING & DRAINAGE LTD",
    "ALIAXIS UK LTD", "GEBERIT SALES LTD", "GROHE LTD",
    "HANSGROHE UK LTD", "ROCA LTD", "MIRA SHOWERS LTD",
    "AQUALISA PRODUCTS LTD", "TRITON SHOWERS LTD",
    "BRISTAN GROUP LTD", "PEGLER YORKSHIRE GROUP LTD",
    "RELIANCE WATER CONTROLS LTD", "SALAMANDER PUMPS LTD",
    "GRUNDFOS PUMPS LTD", "XYLEM WATER SOLUTIONS UK LTD",
    # Harder to identify (no plumbing keywords)
    "FERGUSON ENTERPRISES LTD", "GRAFTON GROUP PLC",
    "HEADLAM GROUP PLC", "SIG PLC", "IBSTOCK PLC",
    "GENUIT GROUP PLC", "ALUMASC GROUP PLC",
    "NORCROS PLC", "MCALPINE & CO LTD", "CARADON GROUP LTD",
]

_SIC_POOLS = {
    "core_wholesale":    ["46740"],
    "wholesale_heating": ["46740", "43220"],
    "wholesale_bath":    ["46740", "47520"],
    "installation":      ["43220"],
    "retail_hardware":   ["47520"],
    "manufacturer":      ["25210", "22230"],
    "mixed":             ["46740", "46730", "43290"],
    "none_obvious":      ["46190", "70100", "82990"],
}

_SIC_ASSIGNMENTS = (
    ["core_wholesale"] * 32
    + ["wholesale_heating"] * 13
    + ["wholesale_bath"] * 10
    + ["installation"] * 9
    + ["retail_hardware"] * 9
    + ["manufacturer"] * 13
    + ["mixed"] * 4
    + ["none_obvious"] * 10
)

_ACCOUNTS_TYPES = (
    ["micro-entity"] * 35
    + ["small"] * 25
    + ["total-exemption-small"] * 10
    + ["medium"] * 8
    + ["full"] * 10
    + ["group"] * 5
    + ["dormant"] * 4
    + ["unaudited-abridged"] * 3
)

_POSTCODES = [
    "SW1A 1AA", "EC1A 1BB", "B1 1AA", "M1 1AA", "LS1 1AA",
    "L1 1AA", "NE1 1AA", "BS1 1AA", "CF10 1AA", "EH1 1AA",
    "G1 1AA", "BT1 1AA", "NG1 1AA", "LE1 1AA", "DE1 1AA",
    "S1 1AA", "BD1 1AA", "HU1 1AA", "DN1 1AA", "WF1 1AA",
    "SE1 1AA", "N1 1AA", "E1 1AA", "W1 1AA", "NW1 1AA",
    "CR0 1AA", "TW1 1AA", "HA1 1AA", "UB1 1AA", "KT1 1AA",
    "RG1 1AA", "GU1 1AA", "PO1 1AA", "SO14 1AA", "BN1 1AA",
    "ME1 1AA", "CT1 1AA", "TN1 1AA", "OX1 1AA", "MK1 1AA",
    "CB1 1AA", "IP1 1AA", "NR1 1AA", "CO1 1AA", "CM1 1AA",
    "SS1 1AA", "LU1 1AA", "AL1 1AA", "SG1 1AA", "HP1 1AA",
    "EX1 1AA", "PL1 1AA", "TQ1 1AA", "TA1 1AA", "BA1 1AA",
    "GL1 1AA", "SN1 1AA", "SP1 1AA", "DT1 1AA", "BH1 1AA",
    "WR1 1AA", "CV1 1AA", "DY1 1AA", "WS1 1AA", "WV1 1AA",
    "ST1 1AA", "SK1 1AA", "CW1 1AA", "CH1 1AA", "WA1 1AA",
    "PR1 1AA", "BB1 1AA", "OL1 1AA", "BL1 1AA", "FY1 1AA",
    "LA1 1AA", "CA1 1AA", "DL1 1AA", "DH1 1AA", "TS1 1AA",
    "SR1 1AA", "YO1 1AA", "HG1 1AA", "HD1 1AA", "HX1 1AA",
    "AB1 1AA", "DD1 1AA", "KY1 1AA", "FK1 1AA", "PA1 1AA",
    "ML1 1AA", "KA1 1AA", "IV1 1AA", "PH1 1AA", "LL1 1AA",
    "SA1 1AA", "NP1 1AA", "LD1 1AA", "SY1 1AA", "HR1 1AA",
]

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

PLUMBING_KEYWORDS = [
    "plumb", "heating", "bathroom", "boiler", "pipe", "radiator",
    "sanitary", "drain", "water", "hvac", "thermal", "gas",
    "shower", "tap", "valve", "pump", "cistern", "waste",
    "merchant", "supply", "supplies", "wholesale",
    "hardware", "building supplies",
]

SIC_DESCRIPTIONS = {
    "46740": "Wholesale plumbing & heating equipment",
    "43220": "Plumbing, heat & air-con installation",
    "47520": "Retail hardware, paints & glass",
    "46730": "Wholesale wood & construction materials",
    "43290": "Other construction installation",
    "25210": "Manufacture of radiators & boilers",
    "22230": "Manufacture of plastic products",
    "46190": "Agents in sale of goods",
    "70100": "Head office activities",
    "82990": "Other business support",
}

COLOURS = [
    "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
    "#db2777", "#0891b2", "#65a30d", "#ea580c", "#4f46e5",
    "#0d9488", "#c026d3", "#e11d48", "#059669", "#6d28d9",
]


def generate_sample_data():
    """Generate a DataFrame of 100 realistic plumbing merchants."""
    random.seed(42)
    rows = []
    for i in range(100):
        name = _NAMES[i]
        sic_key = _SIC_ASSIGNMENTS[i]
        sic_codes = ",".join(_SIC_POOLS[sic_key])
        acct = _ACCOUNTS_TYPES[i % len(_ACCOUNTS_TYPES)]
        pc = _POSTCODES[i % len(_POSTCODES)]
        # Random creation date 1-40 years ago
        days_ago = random.randint(365, 365 * 40)
        created = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        # Confidence score
        has_core_sic = "46740" in sic_codes or "43220" in sic_codes or "47520" in sic_codes
        name_lower = name.lower()
        has_kw = any(kw in name_lower for kw in ["plumb", "heating", "bath", "boiler", "pipe", "drain", "shower"])
        conf = 0
        if has_core_sic:
            conf += random.randint(40, 55)
        if has_kw:
            conf += random.randint(20, 45)
        if not has_core_sic and not has_kw:
            conf = random.randint(5, 25)
        conf = min(conf, 100)

        is_chain = name in {
            "CITY PLUMBING SUPPLIES HOLDINGS LTD", "WOLSELEY UK LTD",
            "PLUMB CENTER LTD", "GRAHAM PLUMBERS MERCHANT LTD",
            "SCREWFIX DIRECT LTD", "TOOLSTATION LTD",
            "WICKES BUILDING SUPPLIES LTD", "TRAVIS PERKINS TRADING CO LTD",
            "JEWSON LTD", "MKM BUILDING SUPPLIES LTD", "SELCO BUILDERS WAREHOUSE LTD",
            "PIMLICO PLUMBERS LTD", "DYNO-ROD LTD", "BATHSTORE LTD",
        }
        branch_count = random.randint(10, 350) if is_chain else 1

        rows.append({
            "company_name": name,
            "company_number": f"{random.randint(1000000, 9999999):07d}",
            "company_status": "active",
            "sic_codes": sic_codes,
            "last_accounts_type": acct,
            "postal_code": pc,
            "date_of_creation": created,
            "confidence": conf,
            "match_type": "core SIC + keyword" if has_core_sic and has_kw
                          else "core SIC only" if has_core_sic
                          else "keyword only" if has_kw
                          else "other",
            "is_chain": is_chain,
            "chain_branch_count": branch_count,
        })
    return pd.DataFrame(rows)


def postcode_to_region(pc):
    if not pc or pd.isna(pc):
        return "Unknown"
    m = re.match(r"^([A-Z]{1,2})", str(pc).strip().upper())
    if not m:
        return "Unknown"
    area = m.group(1)
    return POSTCODE_REGIONS.get(area, POSTCODE_REGIONS.get(area[0], "Unknown"))


# ──────────────────────────────────────────────────────────────────────
# ANALYSIS FUNCTIONS
# ──────────────────────────────────────────────────────────────────────

def analyse_sic(df):
    """Count each SIC code across all merchants."""
    all_codes = []
    for codes_str in df["sic_codes"].fillna(""):
        for c in str(codes_str).split(","):
            c = c.strip()
            if c:
                all_codes.append(c)
    counts = pd.Series(all_codes).value_counts()
    labels = [SIC_DESCRIPTIONS.get(c, c) for c in counts.index]
    return counts, labels


def analyse_keywords(df):
    """Count how many company names contain each plumbing keyword."""
    names = df["company_name"].str.lower()
    kw_counts = {}
    for kw in PLUMBING_KEYWORDS:
        kw_counts[kw] = names.str.contains(kw, na=False).sum()
    s = pd.Series(kw_counts).sort_values(ascending=False)
    return s[s > 0]


def analyse_regions(df):
    """Count merchants per UK region."""
    df = df.copy()
    df["region"] = df["postal_code"].apply(postcode_to_region)
    return df["region"].value_counts()


def analyse_accounts(df):
    """Count merchants per accounts type."""
    return df["last_accounts_type"].value_counts()


def analyse_confidence(df):
    """Bucket confidence scores."""
    bins = [0, 20, 40, 60, 80, 101]
    labels = ["0-20%", "21-40%", "41-60%", "61-80%", "81-100%"]
    df = df.copy()
    df["conf_bucket"] = pd.cut(df["confidence"], bins=bins, labels=labels, right=False)
    return df["conf_bucket"].value_counts().reindex(labels)


def analyse_match_type(df):
    """Count by match type."""
    return df["match_type"].value_counts()


def analyse_age(df):
    """Return company ages in years."""
    dates = pd.to_datetime(df["date_of_creation"], errors="coerce")
    ages = (pd.Timestamp.now() - dates).dt.days / 365.25
    return ages.dropna()


# ──────────────────────────────────────────────────────────────────────
# CHART BUILDERS
# ──────────────────────────────────────────────────────────────────────

def chart_sic_bar(sic_counts, sic_labels):
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=sic_counts.values,
        y=[f"{code} — {lbl}" for code, lbl in zip(sic_counts.index, sic_labels)],
        orientation="h",
        marker_color=COLOURS[:len(sic_counts)],
        text=sic_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        title="SIC Code Distribution",
        xaxis_title="Number of Merchants",
        yaxis=dict(autorange="reversed"),
        height=max(400, len(sic_counts) * 40),
        margin=dict(l=300),
        template="plotly_white",
    )
    return fig


def chart_sic_pie(sic_counts, sic_labels):
    fig = go.Figure(go.Pie(
        labels=[f"{c} — {l}" for c, l in zip(sic_counts.index, sic_labels)],
        values=sic_counts.values,
        hole=0.4,
        marker_colors=COLOURS[:len(sic_counts)],
        textinfo="percent+label",
        textposition="outside",
    ))
    fig.update_layout(
        title="SIC Code Share",
        height=500,
        template="plotly_white",
        showlegend=False,
    )
    return fig


def chart_keywords(kw_counts):
    fig = go.Figure(go.Bar(
        x=kw_counts.values,
        y=kw_counts.index,
        orientation="h",
        marker_color="#2563eb",
        text=kw_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        title="Plumbing Keyword Frequency in Company Names",
        xaxis_title="Number of Merchants",
        yaxis=dict(autorange="reversed"),
        height=max(400, len(kw_counts) * 32),
        margin=dict(l=180),
        template="plotly_white",
    )
    return fig


def chart_regions(region_counts):
    fig = go.Figure(go.Bar(
        x=region_counts.values,
        y=region_counts.index,
        orientation="h",
        marker_color="#16a34a",
        text=region_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        title="Geographic Distribution by UK Region",
        xaxis_title="Number of Merchants",
        yaxis=dict(autorange="reversed"),
        height=max(400, len(region_counts) * 36),
        margin=dict(l=180),
        template="plotly_white",
    )
    return fig


def chart_accounts(acct_counts):
    fig = go.Figure(go.Bar(
        x=acct_counts.index.astype(str),
        y=acct_counts.values,
        marker_color="#d97706",
        text=acct_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        title="Company Size by Accounts Type",
        yaxis_title="Number of Merchants",
        xaxis_tickangle=-35,
        height=420,
        template="plotly_white",
    )
    return fig


def chart_confidence(conf_counts):
    colours = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#16a34a"]
    fig = go.Figure(go.Bar(
        x=conf_counts.index.astype(str),
        y=conf_counts.values,
        marker_color=colours,
        text=conf_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        title="Confidence Score Distribution",
        xaxis_title="Confidence Bucket",
        yaxis_title="Number of Merchants",
        height=400,
        template="plotly_white",
    )
    return fig


def chart_match_type(match_counts):
    fig = go.Figure(go.Pie(
        labels=match_counts.index,
        values=match_counts.values,
        hole=0.45,
        marker_colors=["#2563eb", "#16a34a", "#d97706", "#dc2626"],
        textinfo="value+percent",
    ))
    fig.update_layout(
        title="How Merchants Were Identified",
        height=420,
        template="plotly_white",
    )
    return fig


def chart_age(ages):
    fig = go.Figure(go.Histogram(
        x=ages,
        nbinsx=20,
        marker_color="#7c3aed",
    ))
    fig.update_layout(
        title="Company Age Distribution",
        xaxis_title="Age (years)",
        yaxis_title="Count",
        height=400,
        template="plotly_white",
    )
    return fig


def chart_chain_vs_independent(df):
    chains = df["is_chain"].sum()
    indep = len(df) - chains
    fig = go.Figure(go.Pie(
        labels=["Chain", "Independent"],
        values=[chains, indep],
        hole=0.5,
        marker_colors=["#2563eb", "#16a34a"],
        textinfo="value+percent+label",
    ))
    fig.update_layout(
        title="Chain vs Independent",
        height=380,
        template="plotly_white",
    )
    return fig


# ──────────────────────────────────────────────────────────────────────
# HTML DASHBOARD
# ──────────────────────────────────────────────────────────────────────

def build_dashboard(df, output_path):
    """Run all analyses and write a single interactive HTML dashboard."""
    sic_counts, sic_labels = analyse_sic(df)
    kw_counts = analyse_keywords(df)
    region_counts = analyse_regions(df)
    acct_counts = analyse_accounts(df)
    conf_counts = analyse_confidence(df)
    match_counts = analyse_match_type(df)
    ages = analyse_age(df)

    figures = [
        chart_sic_bar(sic_counts, sic_labels),
        chart_sic_pie(sic_counts, sic_labels),
        chart_keywords(kw_counts),
        chart_regions(region_counts),
        chart_accounts(acct_counts),
        chart_confidence(conf_counts),
        chart_match_type(match_counts),
        chart_age(ages),
        chart_chain_vs_independent(df),
    ]

    # Summary stats
    total = len(df)
    active = (df["company_status"] == "active").sum()
    chains = df["is_chain"].sum()
    avg_conf = df["confidence"].mean()
    median_age = ages.median()
    top_sic = sic_counts.index[0] if not sic_counts.empty else "N/A"
    top_sic_desc = SIC_DESCRIPTIONS.get(top_sic, top_sic)

    # Build HTML
    chart_divs = "\n".join(
        f'<div class="chart-card">{fig.to_html(full_html=False, include_plotlyjs=False)}</div>'
        for fig in figures
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plumbing Merchants Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
         background: #f1f5f9; color: #1e293b; }}
  .header {{ background: linear-gradient(135deg, #1e40af 0%, #7c3aed 100%);
             color: white; padding: 2.5rem 2rem; text-align: center; }}
  .header h1 {{ font-size: 2rem; font-weight: 700; }}
  .header p {{ opacity: 0.85; margin-top: 0.5rem; font-size: 1.05rem; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap: 1rem; padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }}
  .kpi {{ background: white; border-radius: 12px; padding: 1.2rem 1.5rem;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); text-align: center; }}
  .kpi .num {{ font-size: 2rem; font-weight: 700; color: #1e40af; }}
  .kpi .label {{ font-size: 0.85rem; color: #64748b; margin-top: 0.25rem; }}
  .charts {{ max-width: 1400px; margin: 0 auto; padding: 0 2rem 3rem; }}
  .chart-card {{ background: white; border-radius: 12px;
                 box-shadow: 0 1px 3px rgba(0,0,0,.08);
                 padding: 1.5rem; margin-bottom: 1.5rem; }}
  .footer {{ text-align: center; padding: 2rem; color: #94a3b8; font-size: 0.85rem; }}
</style>
</head>
<body>
<div class="header">
  <h1>Plumbing Merchants Analysis Dashboard</h1>
  <p>{total} companies &middot; Generated {datetime.now().strftime('%d %B %Y %H:%M')}</p>
</div>

<div class="kpi-row">
  <div class="kpi"><div class="num">{total}</div><div class="label">Total Merchants</div></div>
  <div class="kpi"><div class="num">{active}</div><div class="label">Active Companies</div></div>
  <div class="kpi"><div class="num">{chains}</div><div class="label">Chain Businesses</div></div>
  <div class="kpi"><div class="num">{avg_conf:.0f}%</div><div class="label">Avg Confidence</div></div>
  <div class="kpi"><div class="num">{median_age:.0f}y</div><div class="label">Median Age</div></div>
  <div class="kpi"><div class="num">{top_sic}</div><div class="label">Top SIC Code</div></div>
</div>

<div class="charts">
{chart_divs}
</div>

<div class="footer">Plumbing Merchant Intelligence &middot; Data from Companies House API</div>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved to: {output_path}")
    print(f"  {total} merchants analysed")
    print(f"  {len(figures)} interactive charts")
    print(f"  Open in browser: file://{os.path.abspath(output_path)}")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate interactive HTML dashboard for plumbing merchants"
    )
    parser.add_argument(
        "-i", "--input", default=None,
        help="Input CSV/Excel of merchants (default: use built-in 100 samples)",
    )
    parser.add_argument(
        "-o", "--output", default="data/merchant_dashboard.html",
        help="Output HTML file (default: data/merchant_dashboard.html)",
    )
    args = parser.parse_args()

    if args.input and os.path.exists(args.input):
        if args.input.endswith(".xlsx"):
            df = pd.read_excel(args.input, engine="openpyxl")
        else:
            df = pd.read_csv(args.input)
        print(f"Loaded {len(df)} merchants from {args.input}")
    else:
        df = generate_sample_data()
        print(f"Using built-in sample of {len(df)} plumbing merchants")
        # Also save as CSV for other scripts to use
        csv_path = os.path.join(os.path.dirname(args.output) or "data", "sample_100_merchants.csv")
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        df.to_csv(csv_path, index=False)
        print(f"Sample data saved to: {csv_path}")

    build_dashboard(df, args.output)


if __name__ == "__main__":
    main()
