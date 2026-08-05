from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

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
    if not catalogue.exists():
        raise FileNotFoundError(f"Catalogue not found: {catalogue}")
    if not images_dir.exists() or not images_dir.is_dir():
        raise FileNotFoundError(f"Images folder not found: {images_dir}")

    df = pd.read_excel(catalogue, sheet_name="Products")
    required = {"Product Title", "Quantity / Pack Size", "Product ID"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    image_lookup = {
        p.name.lower(): p
        for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    }
    rows: list[dict[str, str]] = []

    for _, row in df.iterrows():
        title = clean(row.get("Product Title"))
        quantity = clean(row.get("Quantity / Pack Size"))
        product_id = numeric_product_id(row.get("Product ID"))
        matched_name = clean(row.get("Matched Image Filename")) if "Matched Image Filename" in df.columns else ""

        candidate_names: list[str] = []
        if matched_name:
            candidate_names.append(matched_name)
        candidate_names.extend([
            f"{title}.jpg",
            f"{title}.jpeg",
            f"{title}.png",
            f"{title}.webp",
            f"{title} {quantity}.jpg",
            f"{title} {quantity}.jpeg",
            f"{title} {quantity}.png",
            f"{title} {quantity}.webp",
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

    print(f"Matched {len(rows)} catalogue products to local image files.")
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


def is_admin_page(url: str, store_handle: str) -> bool:
    lowered = url.lower()
    return (
        "admin.shopify.com" in lowered
        and f"/store/{store_handle.lower()}" in lowered
        and "accounts.shopify.com" not in lowered
        and "login" not in lowered
    )


def wait_for_manual_login(
    context: BrowserContext,
    page: Page,
    admin_url: str,
    store_handle: str,
) -> Page:
    try:
        page.goto(admin_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        if page.is_closed():
            page = context.new_page()
            page.goto(admin_url, wait_until="domcontentloaded", timeout=60000)
        else:
            print(f"Initial Shopify navigation warning: {exc}")

    print("\nShopify login opened in the browser.")
    print("1. Complete email/password, account selection and any verification.")
    print("2. Make sure the Shopify admin dashboard for the correct store is visible.")
    input("3. Return to Terminal and press ENTER after the dashboard is visible... ")

    # Shopify may open the authenticated admin in a different tab. Search every
    # open page instead of checking only the original login tab.
    for candidate in reversed(context.pages):
        try:
            if not candidate.is_closed() and is_admin_page(candidate.url, store_handle):
                candidate.bring_to_front()
                print(f"Login confirmed in browser tab: {candidate.url}\n")
                return candidate
        except Exception:
            continue

    # The dashboard might be visible but its URL has not settled yet. Try the
    # store admin URL once in each remaining open tab using the saved session.
    for candidate in reversed(context.pages):
        if candidate.is_closed():
            continue
        try:
            candidate.goto(admin_url, wait_until="domcontentloaded", timeout=60000)
            candidate.wait_for_timeout(2500)
            if is_admin_page(candidate.url, store_handle):
                candidate.bring_to_front()
                print(f"Login confirmed: {candidate.url}\n")
                return candidate
        except Exception:
            continue

    open_urls = [p.url for p in context.pages if not p.is_closed()]
    raise RuntimeError(
        "Shopify login could not be confirmed. Keep the Shopify dashboard open in the automation browser, "
        "then run the command again. Open browser URLs were: " + " | ".join(open_urls)
    )


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


def launch_context(playwright: Any, use_chromium: bool):
    kwargs: dict[str, Any] = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "viewport": {"width": 1440, "height": 1000},
    }
    if not use_chromium:
        kwargs["channel"] = "chrome"
    try:
        return playwright.chromium.launch_persistent_context(**kwargs)
    except Exception as exc:
        if not use_chromium:
            print(f"Could not open installed Google Chrome ({exc}). Falling back to Playwright Chromium.")
            kwargs.pop("channel", None)
            return playwright.chromium.launch_persistent_context(**kwargs)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload matched product images through Shopify Admin using Playwright.")
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("images", type=Path)
    parser.add_argument("--store", required=True, help="Shopify store handle used in admin.shopify.com/store/<handle>")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--replace", action="store_true", help="Upload even when existing media is detected")
    parser.add_argument("--login-only", action="store_true", help="Open Shopify, save the login session, then exit")
    parser.add_argument("--use-playwright-chromium", action="store_true", help="Use bundled Chromium instead of installed Google Chrome")
    args = parser.parse_args()

    rows = load_rows(args.catalogue, args.images)
    if args.limit:
        rows = rows[: args.limit]

    done = completed_ids()
    admin_base = f"https://admin.shopify.com/store/{args.store}"

    with sync_playwright() as p:
        context = launch_context(p, args.use_playwright_chromium)
        page = context.pages[0] if context.pages else context.new_page()
        page = wait_for_manual_login(context, page, admin_base, args.store)

        if args.login_only:
            print("Login session saved. You can now run the upload command without --login-only.")
            context.close()
            return

        for index, row in enumerate(rows, start=1):
            product_id = row["product_id"]
            if product_id in done:
                continue

            record: dict[str, Any] = {**row, "status": "failed", "message": ""}
            try:
                print(f"[{index}/{len(rows)}] {row['title']} — {row['quantity']}")
                page.goto(f"{admin_base}/products/{product_id}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)

                if has_existing_media(page) and not args.replace:
                    record["status"] = "skipped_existing_media"
                    record["message"] = "Product already appears to have media"
                else:
                    upload_image(page, row["image_path"])
                    page.wait_for_timeout(5000)
                    save_product(page)
                    record["status"] = "uploaded"
                    record["message"] = "Image uploaded and product saved"
                    done.add(product_id)
            except Exception as exc:
                record["message"] = str(exc)

            append_checkpoint(record)
            time.sleep(1.5)

        context.close()

    if CHECKPOINT.exists():
        all_records = [json.loads(line) for line in CHECKPOINT.read_text(encoding="utf-8").splitlines() if line.strip()]
        pd.DataFrame(all_records).to_excel(REPORT, index=False)
        print(f"Report saved to {REPORT}")


if __name__ == "__main__":
    main()
