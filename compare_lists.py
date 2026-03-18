"""Compare new_list (v1) vs new_list_v2 — detailed overview in Excel.

Produces a multi-sheet Excel report showing exactly what each script
captured, the conditions used, breakdowns by SIC code / keyword / confidence,
and what v1 caught that v2 didn't (and vice versa).

Usage:
    python compare_lists.py
    python compare_lists.py --v1 data/new_list.xlsx --v2 data/new_list_v2.xlsx
    python compare_lists.py -o data/comparison_report.xlsx
"""
import argparse
import os

import pandas as pd

# ---------------------------------------------------------------------------
# The search conditions used by each version (hardcoded for the report)
# ---------------------------------------------------------------------------
V1_CONFIG = {
    "name": "new_list (v1)",
    "script": "find_plumbing_merchants.py",
    "sic_codes": ["46740", "43220", "47520"],
    "keywords": ["plumb", "heating", "supplies"],
    "confidence_weights": {
        "Top 3 SIC code": "30%",
        "Other plumbing SIC (12 codes)": "10%",
        "Name contains 'plumb'": "25%",
        "Name contains 'heating'": "10%",
        "Name contains 'supplies'": "10%",
        "Name contains 'merchant'": "5%",
        "Name contains 'wholesale'": "5%",
        "Name contains 'bathroom'": "5%",
    },
    "extra_sic_codes": [
        "46130", "46730", "47540", "25210",
        "33200", "43290", "35300", "36000", "37000",
    ],
}

V2_CONFIG = {
    "name": "new_list_v2 (v2)",
    "script": "find_plumbing_merchants_v2.py",
    "sic_codes": ["46740", "43220", "47520"],
    "keywords": ["plumb", "heating"],
    "confidence_weights": {
        "Top 3 SIC code": "40%",
        "Name contains 'plumb'": "35%",
        "Name contains 'heating'": "15%",
        "Name contains 'bathroom'": "10%",
    },
    "extra_sic_codes": [],
}

TOP_3_SIC = {"46740", "43220", "47520"}


def load_list(path):
    """Load an Excel or CSV merchant list."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if path.endswith(".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    return pd.read_csv(path)


def _sic_set(sic_str):
    """Parse a comma-separated SIC string into a set."""
    return {c.strip() for c in str(sic_str).split(",") if c.strip()}


def _has_keyword(name, keywords):
    name_lower = str(name).lower()
    return any(kw in name_lower for kw in keywords)


def build_conditions_sheet():
    """Sheet 1: Side-by-side comparison of search conditions."""
    rows = []

    rows.append({"Attribute": "Script", "v1 (new_list)": V1_CONFIG["script"], "v2 (new_list_v2)": V2_CONFIG["script"]})
    rows.append({"Attribute": "SIC codes searched", "v1 (new_list)": ", ".join(V1_CONFIG["sic_codes"]), "v2 (new_list_v2)": ", ".join(V2_CONFIG["sic_codes"])})
    rows.append({"Attribute": "Name keywords searched", "v1 (new_list)": ", ".join(V1_CONFIG["keywords"]), "v2 (new_list_v2)": ", ".join(V2_CONFIG["keywords"])})
    rows.append({"Attribute": "Extra SIC codes (confidence)", "v1 (new_list)": ", ".join(V1_CONFIG["extra_sic_codes"]) or "None", "v2 (new_list_v2)": "None"})
    rows.append({"Attribute": "Company status filter", "v1 (new_list)": "active only", "v2 (new_list_v2)": "active only"})
    rows.append({"Attribute": "Full profile fetched", "v1 (new_list)": "Yes", "v2 (new_list_v2)": "Yes"})
    rows.append({"Attribute": "", "v1 (new_list)": "", "v2 (new_list_v2)": ""})
    rows.append({"Attribute": "CONFIDENCE WEIGHTS", "v1 (new_list)": "", "v2 (new_list_v2)": ""})

    all_signals = list(dict.fromkeys(
        list(V1_CONFIG["confidence_weights"].keys()) +
        list(V2_CONFIG["confidence_weights"].keys())
    ))
    for signal in all_signals:
        rows.append({
            "Attribute": f"  {signal}",
            "v1 (new_list)": V1_CONFIG["confidence_weights"].get(signal, "—"),
            "v2 (new_list_v2)": V2_CONFIG["confidence_weights"].get(signal, "—"),
        })

    return pd.DataFrame(rows)


def build_totals_sheet(df_v1, df_v2):
    """Sheet 2: Total counts comparison."""
    rows = []

    rows.append({"Metric": "Total companies", "v1": len(df_v1), "v2": len(df_v2), "Difference": len(df_v2) - len(df_v1)})

    # Overlap
    v1_nums = set(df_v1["company_number"].astype(str))
    v2_nums = set(df_v2["company_number"].astype(str))
    overlap = v1_nums & v2_nums
    only_v1 = v1_nums - v2_nums
    only_v2 = v2_nums - v1_nums

    rows.append({"Metric": "In BOTH lists", "v1": len(overlap), "v2": len(overlap), "Difference": 0})
    rows.append({"Metric": "Only in v1 (v2 missed)", "v1": len(only_v1), "v2": 0, "Difference": -len(only_v1)})
    rows.append({"Metric": "Only in v2 (v1 missed)", "v1": 0, "v2": len(only_v2), "Difference": len(only_v2)})
    rows.append({"Metric": "", "v1": "", "v2": "", "Difference": ""})

    # By match type
    rows.append({"Metric": "MATCH TYPE BREAKDOWN", "v1": "", "v2": "", "Difference": ""})
    if "match_type" in df_v1.columns:
        for mt in ["SIC + keyword", "SIC only", "keyword only", "other"]:
            c1 = (df_v1["match_type"] == mt).sum()
            c2 = (df_v2["match_type"] == mt).sum() if "match_type" in df_v2.columns else 0
            rows.append({"Metric": f"  {mt}", "v1": c1, "v2": c2, "Difference": c2 - c1})

    rows.append({"Metric": "", "v1": "", "v2": "", "Difference": ""})

    # By confidence bucket
    rows.append({"Metric": "CONFIDENCE DISTRIBUTION", "v1": "", "v2": "", "Difference": ""})
    if "confidence" in df_v1.columns and "confidence" in df_v2.columns:
        for label, lo, hi in [("High (70-100%)", 70, 100), ("Medium (40-69%)", 40, 69), ("Low (1-39%)", 1, 39), ("Zero (0%)", 0, 0)]:
            c1 = ((df_v1["confidence"] >= lo) & (df_v1["confidence"] <= hi)).sum()
            c2 = ((df_v2["confidence"] >= lo) & (df_v2["confidence"] <= hi)).sum()
            rows.append({"Metric": f"  {label}", "v1": c1, "v2": c2, "Difference": c2 - c1})

    return pd.DataFrame(rows)


def build_sic_breakdown_sheet(df_v1, df_v2):
    """Sheet 3: SIC code breakdown — how many companies per SIC code."""
    # Collect all SIC codes across both lists
    all_sic = set()
    for df in [df_v1, df_v2]:
        if "sic_codes" in df.columns:
            for codes_str in df["sic_codes"].fillna(""):
                all_sic.update(_sic_set(codes_str))

    all_sic.discard("")
    rows = []

    # Top 3 first
    rows.append({"SIC Code": "TOP 3 PLUMBING CODES", "Description": "", "v1 Count": "", "v2 Count": "", "Difference": ""})
    for sic in ["46740", "43220", "47520"]:
        c1 = df_v1["sic_codes"].fillna("").apply(lambda x: sic in _sic_set(x)).sum() if "sic_codes" in df_v1.columns else 0
        c2 = df_v2["sic_codes"].fillna("").apply(lambda x: sic in _sic_set(x)).sum() if "sic_codes" in df_v2.columns else 0
        desc = {
            "46740": "Wholesale of hardware, plumbing and heating equipment",
            "43220": "Plumbing, heat and air-conditioning installation",
            "47520": "Retail sale of hardware, paints and glass",
        }.get(sic, "")
        rows.append({"SIC Code": sic, "Description": desc, "v1 Count": c1, "v2 Count": c2, "Difference": c2 - c1})

    # Total with any top-3
    c1_any = df_v1["sic_codes"].fillna("").apply(lambda x: bool(_sic_set(x) & TOP_3_SIC)).sum() if "sic_codes" in df_v1.columns else 0
    c2_any = df_v2["sic_codes"].fillna("").apply(lambda x: bool(_sic_set(x) & TOP_3_SIC)).sum() if "sic_codes" in df_v2.columns else 0
    rows.append({"SIC Code": "TOTAL (any top 3)", "Description": "", "v1 Count": c1_any, "v2 Count": c2_any, "Difference": c2_any - c1_any})

    # No top-3
    c1_no = len(df_v1) - c1_any
    c2_no = len(df_v2) - c2_any
    rows.append({"SIC Code": "NO top-3 SIC code", "Description": "Matched by keyword only", "v1 Count": c1_no, "v2 Count": c2_no, "Difference": c2_no - c1_no})

    rows.append({"SIC Code": "", "Description": "", "v1 Count": "", "v2 Count": "", "Difference": ""})
    rows.append({"SIC Code": "ALL SIC CODES (top 20)", "Description": "", "v1 Count": "", "v2 Count": "", "Difference": ""})

    # Full SIC frequency for both
    sic_counts = {}
    for sic in all_sic:
        c1 = df_v1["sic_codes"].fillna("").apply(lambda x, s=sic: s in _sic_set(x)).sum() if "sic_codes" in df_v1.columns else 0
        c2 = df_v2["sic_codes"].fillna("").apply(lambda x, s=sic: s in _sic_set(x)).sum() if "sic_codes" in df_v2.columns else 0
        sic_counts[sic] = (c1, c2)

    # Sort by combined count
    sorted_sics = sorted(sic_counts.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)
    for sic, (c1, c2) in sorted_sics[:20]:
        rows.append({"SIC Code": sic, "Description": "", "v1 Count": c1, "v2 Count": c2, "Difference": c2 - c1})

    rows.append({"SIC Code": "", "Description": "", "v1 Count": "", "v2 Count": "", "Difference": ""})
    rows.append({"SIC Code": f"Total unique SIC codes", "Description": "", "v1 Count": len([s for s, (c1, _) in sic_counts.items() if c1 > 0]), "v2 Count": len([s for s, (_, c2) in sic_counts.items() if c2 > 0]), "Difference": ""})

    return pd.DataFrame(rows)


def build_keyword_breakdown_sheet(df_v1, df_v2):
    """Sheet 4: Keyword presence in company names."""
    all_keywords = ["plumb", "heating", "supplies", "supply", "merchant",
                    "wholesale", "bathroom", "boiler", "pipe", "radiator",
                    "drain", "gas", "water", "trade", "hardware", "builders"]

    rows = []
    rows.append({"Keyword": "KEYWORD IN COMPANY NAME", "v1 Count": "", "v1 %": "", "v2 Count": "", "v2 %": "", "Difference": ""})

    for kw in all_keywords:
        c1 = df_v1["company_name"].fillna("").str.lower().str.contains(kw, na=False).sum()
        c2 = df_v2["company_name"].fillna("").str.lower().str.contains(kw, na=False).sum()
        p1 = f"{c1 / len(df_v1) * 100:.1f}%" if len(df_v1) > 0 else "—"
        p2 = f"{c2 / len(df_v2) * 100:.1f}%" if len(df_v2) > 0 else "—"
        searched_v1 = "  ← SEARCHED" if kw in ["plumb", "heating", "supplies"] else ""
        searched_v2 = "  ← SEARCHED" if kw in ["plumb", "heating"] else ""
        rows.append({
            "Keyword": kw,
            "v1 Count": c1, "v1 %": p1 + searched_v1,
            "v2 Count": c2, "v2 %": p2 + searched_v2,
            "Difference": c2 - c1,
        })

    # Companies with NO plumbing keyword at all
    plumbing_kws = ["plumb", "heating", "bathroom", "boiler", "pipe", "radiator",
                    "drain", "gas", "water", "supplies", "merchant", "wholesale"]
    no_kw_v1 = (~df_v1["company_name"].fillna("").str.lower().apply(
        lambda n: any(k in n for k in plumbing_kws)
    )).sum()
    no_kw_v2 = (~df_v2["company_name"].fillna("").str.lower().apply(
        lambda n: any(k in n for k in plumbing_kws)
    )).sum()
    rows.append({"Keyword": "", "v1 Count": "", "v1 %": "", "v2 Count": "", "v2 %": "", "Difference": ""})
    rows.append({
        "Keyword": "NO plumbing keyword in name",
        "v1 Count": no_kw_v1, "v1 %": f"{no_kw_v1 / len(df_v1) * 100:.1f}%" if len(df_v1) else "—",
        "v2 Count": no_kw_v2, "v2 %": f"{no_kw_v2 / len(df_v2) * 100:.1f}%" if len(df_v2) else "—",
        "Difference": no_kw_v2 - no_kw_v1,
    })

    return pd.DataFrame(rows)


def build_only_in_v1_sheet(df_v1, df_v2):
    """Sheet 5: Companies in v1 but NOT in v2 — what v2 missed."""
    v1_nums = set(df_v1["company_number"].astype(str))
    v2_nums = set(df_v2["company_number"].astype(str))
    only_v1 = v1_nums - v2_nums

    if not only_v1:
        return pd.DataFrame({"Note": ["No companies are exclusive to v1 — v2 captured everything v1 did"]})

    mask = df_v1["company_number"].astype(str).isin(only_v1)
    subset = df_v1[mask].copy()

    # Keep useful columns
    keep_cols = ["company_name", "company_number", "sic_codes", "company_status",
                 "postal_code", "locality", "region"]
    if "confidence" in subset.columns:
        keep_cols.append("confidence")
    if "match_type" in subset.columns:
        keep_cols.append("match_type")
    if "source" in subset.columns:
        keep_cols.append("source")
    if "sic_descriptions" in subset.columns:
        keep_cols.append("sic_descriptions")

    available = [c for c in keep_cols if c in subset.columns]
    subset = subset[available].sort_values("company_name")

    return subset


def build_only_in_v2_sheet(df_v1, df_v2):
    """Sheet 6: Companies in v2 but NOT in v1 — what v1 missed."""
    v1_nums = set(df_v1["company_number"].astype(str))
    v2_nums = set(df_v2["company_number"].astype(str))
    only_v2 = v2_nums - v1_nums

    if not only_v2:
        return pd.DataFrame({"Note": ["No companies are exclusive to v2 — v1 captured everything v2 did"]})

    mask = df_v2["company_number"].astype(str).isin(only_v2)
    subset = df_v2[mask].copy()

    keep_cols = ["company_name", "company_number", "sic_codes", "company_status",
                 "postal_code", "locality", "region"]
    if "confidence" in subset.columns:
        keep_cols.append("confidence")
    if "match_type" in subset.columns:
        keep_cols.append("match_type")
    if "source" in subset.columns:
        keep_cols.append("source")
    if "sic_descriptions" in subset.columns:
        keep_cols.append("sic_descriptions")

    available = [c for c in keep_cols if c in subset.columns]
    subset = subset[available].sort_values("company_name")

    return subset


def build_chain_comparison_sheet(df_v1, df_v2):
    """Sheet 7: Chain business comparison."""
    rows = []

    for label, df in [("v1", df_v1), ("v2", df_v2)]:
        if "is_chain" not in df.columns:
            continue

        chains = df.loc[df["is_chain"], "chain_name"].nunique() if "chain_name" in df.columns else 0
        branches = df["is_chain"].sum()
        independent = (~df["is_chain"]).sum()

        rows.append({"Metric": f"{label} — Chain businesses", "Value": chains})
        rows.append({"Metric": f"{label} — Total chain branches", "Value": branches})
        rows.append({"Metric": f"{label} — Independent businesses", "Value": independent})
        rows.append({"Metric": "", "Value": ""})

        if chains > 0 and "chain_name" in df.columns:
            top = (
                df.loc[df["is_chain"]]
                .groupby("chain_name")["chain_branch_count"]
                .first()
                .sort_values(ascending=False)
                .head(15)
            )
            rows.append({"Metric": f"{label} — TOP CHAINS", "Value": ""})
            for name, count in top.items():
                rows.append({"Metric": f"  {name}", "Value": f"{count} branches"})
            rows.append({"Metric": "", "Value": ""})

    return pd.DataFrame(rows)


def build_region_comparison_sheet(df_v1, df_v2):
    """Sheet 8: Geographic distribution comparison."""
    region_col = None
    for candidate in ["region", "Region"]:
        if candidate in df_v1.columns:
            region_col = candidate
            break

    if region_col is None:
        return pd.DataFrame({"Note": ["No region column found in data"]})

    v1_regions = df_v1[region_col].fillna("Unknown").value_counts()
    v2_regions = df_v2[region_col].fillna("Unknown").value_counts() if region_col in df_v2.columns else pd.Series(dtype=int)

    all_regions = sorted(set(v1_regions.index) | set(v2_regions.index))
    rows = []
    for region in all_regions:
        c1 = v1_regions.get(region, 0)
        c2 = v2_regions.get(region, 0)
        rows.append({
            "Region": region,
            "v1 Count": c1,
            "v2 Count": c2,
            "Difference": c2 - c1,
        })

    df_out = pd.DataFrame(rows).sort_values("v1 Count", ascending=False)
    return df_out


def main():
    parser = argparse.ArgumentParser(
        description="Compare new_list (v1) vs new_list_v2 — Excel report"
    )
    parser.add_argument(
        "--v1", default="data/new_list.xlsx",
        help="Path to v1 merchant list (default: data/new_list.xlsx)",
    )
    parser.add_argument(
        "--v2", default="data/new_list_v2.xlsx",
        help="Path to v2 merchant list (default: data/new_list_v2.xlsx)",
    )
    parser.add_argument(
        "-o", "--output", default="data/comparison_report.xlsx",
        help="Output Excel report (default: data/comparison_report.xlsx)",
    )
    args = parser.parse_args()

    print(f"Loading v1: {args.v1}")
    df_v1 = load_list(args.v1)
    print(f"  → {len(df_v1)} companies")

    print(f"Loading v2: {args.v2}")
    df_v2 = load_list(args.v2)
    print(f"  → {len(df_v2)} companies")

    # Build all sheets
    sheets = {
        "1. Conditions": build_conditions_sheet(),
        "2. Totals": build_totals_sheet(df_v1, df_v2),
        "3. SIC Breakdown": build_sic_breakdown_sheet(df_v1, df_v2),
        "4. Keyword Breakdown": build_keyword_breakdown_sheet(df_v1, df_v2),
        "5. Only in v1": build_only_in_v1_sheet(df_v1, df_v2),
        "6. Only in v2": build_only_in_v2_sheet(df_v1, df_v2),
        "7. Chains": build_chain_comparison_sheet(df_v1, df_v2),
        "8. Regions": build_region_comparison_sheet(df_v1, df_v2),
    }

    # Write Excel
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
        for sheet_name, df_sheet in sheets.items():
            df_sheet.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\nReport saved to {args.output}")
    print(f"\nSheets:")
    for name, df_sheet in sheets.items():
        print(f"  {name:<25} ({len(df_sheet)} rows)")

    # Print quick summary to terminal
    v1_nums = set(df_v1["company_number"].astype(str))
    v2_nums = set(df_v2["company_number"].astype(str))
    print(f"\n{'='*50}")
    print(f"QUICK SUMMARY")
    print(f"{'='*50}")
    print(f"v1 total:           {len(df_v1)}")
    print(f"v2 total:           {len(df_v2)}")
    print(f"In both:            {len(v1_nums & v2_nums)}")
    print(f"Only in v1:         {len(v1_nums - v2_nums)}")
    print(f"Only in v2:         {len(v2_nums - v1_nums)}")
    print(f"v2 dropped:         {len(v1_nums - v2_nums)} companies that v1 had")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
