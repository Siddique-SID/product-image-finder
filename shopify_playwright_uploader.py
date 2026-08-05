from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

CHECKPOINT = Path("output/reports/shopify_playwright_checkpoint.jsonl")
REPORT = Path("output/reports/shopify_playwright_report.xlsx")
PROFILE_DIR = Path(".shopify-browser-profile")


def clean(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def numeric_product_id(value: Any) -> str:
    text = clean(value)
    match = re.search(r"(\d+)$", text)
    return match.group(1) if match else ""


def load_rows(catalogue: Path, images_dir: Path) -> list[dict[str, str]]:
    df = pd.read_excel(catalogue, sheet_name="Products")
    required = {"Product Title", "Quantity / Pack Size", "Product ID"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    image_lookup = {p.name.lower(): p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}}
    rows: list[dict[str, str]] = []

    for _, row in df.iterrows():
        title = clean(row.get("Product Title"))
        quantity = clean(row.get("Quantity / Pack Size"))
        product_id = numeric_product_id(row.get("Product ID"))
        matched_name = clean(row.get("Matched Image Filename")) if "Matched Image Filename" in df.columns else ""

        candidate_names = []
        if matched_name:
            candidate_names.append(matched_name)
        candidate_names.extend([
            f"{title}.jpg",
            f"{title}.jpeg",
            f"{title}.png",
            f"{title}.webp",
            f"{title} {quantity}.jpg",
            f"{title} {quantity}.png",
        ])

        image_path: Path | None = None
        for candidate in candidate_names:
            hit = image_lookup.get(candidate.lower())
            if hit:
                image_path = hit
                break

        if title and product_id and image_path:
            rows.append({
                "title": title,
                "quantity": quantity,
                "product_id": product_id,
                "image_path": str(image_path.resolve()),
            })
    return rows


def completed_ids() -> set[str]:
    if not CHECKPOINT.exists():
        return set()
    done: set[str] = set()
    for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("status") == "uploaded":
                done.add(str(row.get("product_id")))
        except json.JSONDecodeError:
            continue
    return done


def append_checkpoint(record: dict[str, Any]) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def wait_for_login(page: Page, admin_url: str) -> None:
    page.goto(admin_url, wait_until="domcontentloaded")
    if "login" in page.url.lower() or "accounts.shopify.com" in page.url.lower():
        print("Log in to Shopify in the opened browser. The script will continue after the admin page loads.")
    page.wait_for_url(re.compile(r"https://admin\.shopify\.com/store/.+"), timeout=0)


def has_existing_media(page: Page) -> bool:
    selectors = [
        '[data-testid*="media"] img',
        'section:has-text("Media") img',
        'div:has-text("Media") img',
    ]
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
    return False


def upload_image(page: Page, image_path: str) -> None:
    file_inputs = page.locator('input[type="file"]')
    if file_inputs.count() > 0:
        file_inputs.first.set_input_files(image_path)
        return

    button_candidates = [
        page.get_by_role("button", name=re.compile(r"add media|upload|add image", re.I)),
        page.get_by_text(re.compile(r"add media|upload files|add image", re.I)),
    ]
    for locator in button_candidates:
        try:
            if locator.count() > 0:
                with page.expect_file_chooser(timeout=5000) as chooser_info:
                    locator.first.click()
                chooser_info.value.set_files(image_path)
                return
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError("Could not find Shopify media upload control")


def save_product(page: Page) -> None:
    save = page.get_by_role("button", name=re.compile(r"^save$", re.I))
    if save.count() == 0:
        save = page.get_by_text(re.compile(r"^save$", re.I))
    if save.count() == 0:
        raise RuntimeError("Could not find Save button")
    save.first.click()
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload matched product images through Shopify Admin using Playwright.")
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("images", type=Path)
    parser.add_argument("--store", required=True, help="Shopify store handle used in admin.shopify.com/store/<handle>")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--headed", action="store_true", help="Kept for compatibility; browser is headed by default")
    parser.add_argument("--replace", action="store_true", help="Upload even when existing media is detected")
    args = parser.parse_args()

    rows = load_rows(args.catalogue, args.images)
    if args.limit:
        rows = rows[: args.limit]

    done = completed_ids()
    records: list[dict[str, Any]] = []
    admin_base = f"https://admin.shopify.com/store/{args.store}"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        wait_for_login(page, admin_base)

        for index, row in enumerate(rows, start=1):
            product_id = row["product_id"]
            if product_id in done:
                continue

            record: dict[str, Any] = {**row, "status": "failed", "message": ""}
            try:
                print(f"[{index}/{len(rows)}] {row['title']} — {row['quantity']}")
                page.goto(f"{admin_base}/products/{product_id}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)

                if has_existing_media(page) and not args.replace:
                    record["status"] = "skipped_existing_media"
                    record["message"] = "Product already appears to have media"
                else:
                    upload_image(page, row["image_path"])
                    page.wait_for_timeout(4000)
                    save_product(page)
                    record["status"] = "uploaded"
                    record["message"] = "Image uploaded and product saved"
                    done.add(product_id)
            except Exception as exc:
                record["message"] = str(exc)

            append_checkpoint(record)
            records.append(record)
            time.sleep(1.5)

        context.close()

    if CHECKPOINT.exists():
        all_records = [json.loads(line) for line in CHECKPOINT.read_text(encoding="utf-8").splitlines() if line.strip()]
        pd.DataFrame(all_records).to_excel(REPORT, index=False)
        print(f"Report saved to {REPORT}")


if __name__ == "__main__":
    main()
