from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pandas as pd


def clean(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge matched product images back into the original catalogue workbook.")
    parser.add_argument("catalogue", type=Path, help="Original catalogue .xlsx file")
    parser.add_argument("--report", type=Path, default=Path("output/reports/product_image_results.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("output/reports/catalogue_with_images.xlsx"))
    parser.add_argument("--base-url", default="", help="Public URL prefix for uploaded images, e.g. https://cdn.example.com/products/")
    parser.add_argument("--name-only", action="store_true", help="Match using product title only. Default also checks quantity when available.")
    args = parser.parse_args()

    if not args.catalogue.exists():
        raise FileNotFoundError(f"Catalogue not found: {args.catalogue}")
    if not args.report.exists():
        raise FileNotFoundError(f"Image report not found: {args.report}")

    xls = pd.ExcelFile(args.catalogue)
    sheets = {name: pd.read_excel(args.catalogue, sheet_name=name) for name in xls.sheet_names}
    if "Products" not in sheets:
        raise ValueError(f"Expected a 'Products' sheet. Found: {xls.sheet_names}")

    products = sheets["Products"].copy()
    report = pd.read_excel(args.report)
    report = report[report.get("Status", "") == "matched"].copy()

    required_product_cols = {"Product Title", "Quantity / Pack Size"}
    missing = required_product_cols - set(products.columns)
    if missing:
        raise ValueError(f"Products sheet is missing columns: {sorted(missing)}")
    required_report_cols = {"Product Name", "Quantity", "Image Filename"}
    missing = required_report_cols - set(report.columns)
    if missing:
        raise ValueError(f"Report is missing columns: {sorted(missing)}")

    products["__name_key"] = products["Product Title"].map(norm)
    products["__qty_key"] = products["Quantity / Pack Size"].map(norm)
    report["__name_key"] = report["Product Name"].map(norm)
    report["__qty_key"] = report["Quantity"].map(norm)

    if args.name_only:
        report = report.sort_values("Score", ascending=False).drop_duplicates("__name_key")
        lookup = report.set_index("__name_key").to_dict("index")
        keys = products["__name_key"]
    else:
        report["__match_key"] = report["__name_key"] + "|" + report["__qty_key"]
        report = report.sort_values("Score", ascending=False).drop_duplicates("__match_key")
        lookup = report.set_index("__match_key").to_dict("index")
        keys = products["__name_key"] + "|" + products["__qty_key"]

    if "Local Image Path" not in products.columns:
        insert_at = products.columns.get_loc("Primary Image URL") + 1 if "Primary Image URL" in products.columns else len(products.columns)
        products.insert(insert_at, "Local Image Path", "")

    merged = 0
    for idx, key in keys.items():
        match = lookup.get(key)
        if not match:
            continue
        filename = clean(match.get("Image Filename"))
        if not filename:
            continue
        local_path = f"output/images/{filename}"
        products.at[idx, "Local Image Path"] = local_path
        if args.base_url:
            image_url = args.base_url.rstrip("/") + "/" + filename
            products.at[idx, "Primary Image URL"] = image_url
        elif not clean(products.at[idx, "Primary Image URL"]):
            products.at[idx, "Primary Image URL"] = local_path
        products.at[idx, "Has Image"] = "Yes"
        merged += 1

    products = products.drop(columns=["__name_key", "__qty_key"], errors="ignore")
    sheets["Products"] = products

    if "Needs Images" in sheets:
        remaining = products[products["Has Image"].astype(str).str.lower().ne("yes")]
        keep = [c for c in sheets["Needs Images"].columns if c in remaining.columns]
        sheets["Needs Images"] = remaining[keep].copy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    print(f"Merged {merged} matched images into: {args.output}")
    if not args.base_url:
        print("Note: Primary Image URL currently contains local paths. Shopify/Lovable need public HTTPS image URLs.")


if __name__ == "__main__":
    main()
