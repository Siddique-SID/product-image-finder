from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from rapidfuzz.fuzz import ratio
from tqdm import tqdm

load_dotenv()

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-07")
SHOP_DOMAIN = os.getenv("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
ACCESS_TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "").strip()


def normalise(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def clean(value: Any) -> str:
    return "" if pd.isna(value) else re.sub(r"\s+", " ", str(value)).strip()


def to_product_gid(value: Any) -> str:
    raw = clean(value)
    if not raw:
        return ""
    if raw.startswith("gid://shopify/Product/"):
        return raw
    digits = re.sub(r"\D", "", raw)
    return f"gid://shopify/Product/{digits}" if digits else ""


def graphql(query: str, variables: dict[str, Any], retries: int = 6) -> dict[str, Any]:
    if not SHOP_DOMAIN or not ACCESS_TOKEN:
        raise RuntimeError("Set SHOPIFY_SHOP_DOMAIN and SHOPIFY_ADMIN_ACCESS_TOKEN in .env")
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": ACCESS_TOKEN}
    for attempt in range(retries):
        response = requests.post(url, headers=headers, json={"query": query, "variables": variables}, timeout=90)
        if response.status_code == 429:
            time.sleep(min(2 ** attempt, 30))
            continue
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(json.dumps(payload["errors"], indent=2))
        return payload["data"]
    raise RuntimeError("Shopify rate limit retries exhausted")


def verify_connection() -> str:
    data = graphql("query { shop { name myshopifyDomain } }", {})
    shop = data["shop"]
    return f"{shop['name']} ({shop['myshopifyDomain']})"


def product_has_media(product_gid: str) -> bool:
    query = """
    query ProductMedia($id: ID!) {
      product(id: $id) {
        id
        media(first: 1) { nodes { id } }
      }
    }
    """
    data = graphql(query, {"id": product_gid})
    product = data.get("product")
    if not product:
        raise RuntimeError(f"Shopify product not found: {product_gid}")
    return bool(product["media"]["nodes"])


def create_staged_target(image_path: Path) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    mutation = """
    mutation StagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets { url resourceUrl parameters { name value } }
        userErrors { field message }
      }
    }
    """
    variables = {
        "input": [{
            "filename": image_path.name,
            "mimeType": mime_type,
            "httpMethod": "POST",
            "resource": "PRODUCT_IMAGE"
        }]
    }
    result = graphql(mutation, variables)["stagedUploadsCreate"]
    if result["userErrors"]:
        raise RuntimeError(str(result["userErrors"]))
    target = result["stagedTargets"][0]
    target["mimeType"] = mime_type
    return target


def upload_to_stage(target: dict[str, Any], image_path: Path) -> None:
    fields = {item["name"]: item["value"] for item in target["parameters"]}
    with image_path.open("rb") as handle:
        files = {"file": (image_path.name, handle, target["mimeType"])}
        response = requests.post(target["url"], data=fields, files=files, timeout=180)
    response.raise_for_status()


def attach_media(product_gid: str, resource_url: str, alt_text: str) -> dict[str, Any]:
    mutation = """
    mutation ProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $productId, media: $media) {
        media {
          id
          status
          alt
          ... on MediaImage { image { url } }
        }
        mediaUserErrors { field message }
      }
    }
    """
    variables = {
        "productId": product_gid,
        "media": [{
            "mediaContentType": "IMAGE",
            "originalSource": resource_url,
            "alt": alt_text[:512]
        }]
    }
    result = graphql(mutation, variables)["productCreateMedia"]
    if result["mediaUserErrors"]:
        raise RuntimeError(str(result["mediaUserErrors"]))
    return result["media"][0]


def build_image_index(images_dir: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in images_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            index.setdefault(normalise(path.stem), []).append(path)
    return index


def choose_image(title: str, quantity: str, filename_hint: str, index: dict[str, list[Path]]) -> tuple[Path | None, int]:
    candidates = [
        normalise(Path(filename_hint).stem) if filename_hint else "",
        normalise(f"{title} {quantity}"),
        normalise(title),
    ]
    for key in candidates:
        if key and key in index:
            return index[key][0], 100

    target = normalise(f"{title} {quantity}")
    best_path: Path | None = None
    best_score = 0
    for key, paths in index.items():
        score = int(ratio(target, key))
        if score > best_score:
            best_score = score
            best_path = paths[0]
    return (best_path, best_score) if best_score >= 90 else (None, best_score)


def load_catalogue(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, sheet_name="Products")
    required = {"Product Title", "Quantity / Pack Size", "Product ID"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    return frame


def run(catalogue: Path, images_dir: Path, limit: int | None, replace: bool, dry_run: bool) -> None:
    print(f"Connected to Shopify: {verify_connection()}")
    products = load_catalogue(catalogue)
    if limit:
        products = products.head(limit)
    image_index = build_image_index(images_dir)
    if not image_index:
        raise RuntimeError(f"No images found in {images_dir}")

    output_dir = Path("output/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / "shopify_upload_checkpoint.jsonl"
    completed: set[str] = set()
    rows: list[dict[str, Any]] = []
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                rows.append(item)
                if item.get("Status") in {"uploaded", "skipped_existing", "dry_run"}:
                    completed.add(item.get("Product GID", ""))

    for _, row in tqdm(products.iterrows(), total=len(products), desc="Uploading product images"):
        title = clean(row.get("Product Title"))
        quantity = clean(row.get("Quantity / Pack Size"))
        product_gid = to_product_gid(row.get("Product ID"))
        filename_hint = clean(row.get("Matched Image Filename"))

        if not product_gid:
            record = {"Product Title": title, "Quantity": quantity, "Status": "failed", "Reason": "Missing Product ID"}
        elif product_gid in completed:
            continue
        else:
            image_path, score = choose_image(title, quantity, filename_hint, image_index)
            record = {
                "Product Title": title,
                "Quantity": quantity,
                "Product GID": product_gid,
                "Matched Image": str(image_path) if image_path else "",
                "Match Score": score,
            }
            if not image_path:
                record.update({"Status": "failed", "Reason": "No image matched at 90% or above"})
            else:
                try:
                    if not replace and product_has_media(product_gid):
                        record.update({"Status": "skipped_existing", "Reason": "Product already has media"})
                    elif dry_run:
                        record.update({"Status": "dry_run", "Reason": "Validated only; no upload performed"})
                    else:
                        target = create_staged_target(image_path)
                        upload_to_stage(target, image_path)
                        media = attach_media(product_gid, target["resourceUrl"], f"{title} {quantity}".strip())
                        record.update({
                            "Status": "uploaded",
                            "Reason": "",
                            "Media ID": media.get("id", ""),
                            "Media Status": media.get("status", ""),
                            "Shopify Image URL": ((media.get("image") or {}).get("url") or ""),
                        })
                        time.sleep(0.4)
                except Exception as exc:
                    record.update({"Status": "failed", "Reason": str(exc)})

        with checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        rows.append(record)

    report = pd.DataFrame(rows)
    report.to_excel(output_dir / "shopify_image_upload_report.xlsx", index=False)
    report.to_csv(output_dir / "shopify_image_upload_report.csv", index=False)
    print(f"Report written to {output_dir / 'shopify_image_upload_report.xlsx'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Match local catalogue images and upload them directly to Shopify products.")
    parser.add_argument("catalogue", type=Path, help="Lovable catalogue .xlsx file")
    parser.add_argument("images_dir", type=Path, help="Folder containing renamed product images")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N catalogue rows")
    parser.add_argument("--replace", action="store_true", help="Upload even when the Shopify product already has media")
    parser.add_argument("--dry-run", action="store_true", help="Match and validate without uploading")
    args = parser.parse_args()
    run(args.catalogue, args.images_dir, args.limit, args.replace, args.dry_run)


if __name__ == "__main__":
    main()
