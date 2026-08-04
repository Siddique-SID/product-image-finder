from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import cv2
import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageFile
from rapidfuzz.fuzz import token_set_ratio
from tqdm import tqdm

from config import SETTINGS

ImageFile.LOAD_TRUNCATED_IMAGES = True
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


@dataclass
class Candidate:
    image_url: str
    page_url: str = ""
    title: str = ""
    source: str = "duckduckgo"
    width: int = 0
    height: int = 0
    white_ratio: float = 0.0
    sharpness: float = 0.0
    text_score: float = 0.0
    quality_score: float = 0.0
    ai_score: float = 0.0
    language: str = "unknown"
    reason: str = ""

    @property
    def final_score(self) -> float:
        return round(self.quality_score * 0.45 + self.text_score * 0.30 + self.ai_score * 0.25, 2)


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return text[:150] or "product"


def find_columns(df: pd.DataFrame) -> tuple[str, str]:
    normalized = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in df.columns}
    name_keys = ("productname", "producttitle", "title", "name", "product")
    qty_keys = ("quantity", "packsize", "size", "weight", "qty")
    name_col = next((normalized[k] for k in name_keys if k in normalized), None)
    qty_col = next((normalized[k] for k in qty_keys if k in normalized), None)
    if not name_col or not qty_col:
        raise ValueError(f"Could not identify product and quantity columns. Found: {list(df.columns)}")
    return name_col, qty_col


def load_products(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        raise ValueError("Input must be .xlsx, .xls or .csv")
    name_col, qty_col = find_columns(df)
    out = df[[name_col, qty_col]].copy()
    out.columns = ["Product Name", "Quantity"]
    out["Product Name"] = out["Product Name"].map(clean_text)
    out["Quantity"] = out["Quantity"].map(clean_text)
    out = out[(out["Product Name"] != "")].drop_duplicates().reset_index(drop=True)
    return out


def ddg_search(query: str, limit: int) -> list[Candidate]:
    session = requests.Session()
    session.headers.update(HEADERS)
    html = session.get("https://duckduckgo.com/", params={"q": query}, timeout=SETTINGS.request_timeout).text
    match = re.search(r'vqd=["\']?([\d-]+)', html)
    if not match:
        return []
    params = {"l": "uk-en", "o": "json", "q": query, "vqd": match.group(1), "f": ",,,", "p": "1"}
    response = session.get("https://duckduckgo.com/i.js", params=params, timeout=SETTINGS.request_timeout)
    response.raise_for_status()
    results = response.json().get("results", [])
    candidates: list[Candidate] = []
    for row in results[:limit]:
        url = row.get("image")
        if url:
            candidates.append(Candidate(image_url=url, page_url=row.get("url", ""), title=row.get("title", "")))
    return candidates


def search_candidates(product: str, quantity: str) -> list[Candidate]:
    queries = [
        f'"{product}" "{quantity}" product white background',
        f'"{product}" "{quantity}" packshot',
        f'{product} {quantity} English packaging',
        f'{product} {quantity} Arabic packaging',
    ]
    seen: set[str] = set()
    results: list[Candidate] = []
    for query in queries:
        try:
            for item in ddg_search(query, SETTINGS.max_candidates):
                if item.image_url not in seen:
                    seen.add(item.image_url)
                    results.append(item)
                if len(results) >= SETTINGS.max_candidates:
                    return results
        except requests.RequestException:
            continue
    return results


def download_bytes(url: str) -> bytes:
    response = requests.get(url, headers=HEADERS, timeout=SETTINGS.request_timeout, allow_redirects=True)
    response.raise_for_status()
    if "image" not in response.headers.get("content-type", "").lower():
        raise ValueError("URL did not return an image")
    if len(response.content) > 15_000_000:
        raise ValueError("Image is too large")
    return response.content


def inspect_image(raw: bytes) -> tuple[Image.Image, dict[str, float]]:
    image = Image.open(BytesIO(raw)).convert("RGB")
    width, height = image.size
    arr = np.asarray(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    border = max(1, min(width, height) // 12)
    border_pixels = np.concatenate([
        arr[:border].reshape(-1, 3), arr[-border:].reshape(-1, 3),
        arr[:, :border].reshape(-1, 3), arr[:, -border:].reshape(-1, 3),
    ])
    white_ratio = float(np.mean(np.all(border_pixels >= 238, axis=1)))
    resolution_score = min(100.0, ((width * height) / (1200 * 1200)) * 100)
    sharpness_score = min(100.0, sharpness / 4)
    white_score = min(100.0, white_ratio * 125)
    quality_score = resolution_score * 0.35 + sharpness_score * 0.25 + white_score * 0.40
    return image, {"width": width, "height": height, "sharpness": sharpness, "white_ratio": white_ratio, "quality_score": quality_score}


def textual_score(candidate: Candidate, product: str, quantity: str) -> float:
    haystack = f"{candidate.title} {candidate.page_url} {candidate.image_url}"
    product_score = token_set_ratio(product.lower(), haystack.lower())
    qty_norm = re.sub(r"\s+", "", quantity.lower())
    qty_score = 100 if qty_norm and qty_norm in re.sub(r"\s+", "", haystack.lower()) else 35
    return product_score * 0.72 + qty_score * 0.28


def ai_verify(raw: bytes, product: str, quantity: str) -> dict[str, Any]:
    if not SETTINGS.openai_api_key:
        return {"score": 0, "language": "unknown", "reason": "AI verification disabled"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=SETTINGS.openai_api_key)
        encoded = base64.b64encode(raw).decode("ascii")
        prompt = f"""Inspect this retail product image for a catalogue. Requested product: {product}. Requested quantity: {quantity}.
Return JSON only with keys score (0-100), exact_product (boolean), exact_quantity (boolean), clean_background (boolean), language (English/Arabic/Urdu/Other/Unknown), and reason. Prefer English packaging but accept Arabic or Urdu. Penalise wrong variants, wrong quantity, shelf photos, collages, watermarks, and unclear images."""
        response = client.responses.create(
            model=SETTINGS.openai_vision_model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"}]}],
        )
        text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(text)
        return {"score": float(data.get("score", 0)), "language": data.get("language", "unknown"), "reason": data.get("reason", "")}
    except Exception as exc:
        return {"score": 0, "language": "unknown", "reason": f"AI error: {exc}"}


def choose_best(product: str, quantity: str) -> tuple[Candidate | None, bytes | None]:
    best: tuple[Candidate, bytes] | None = None
    for candidate in search_candidates(product, quantity):
        try:
            raw = download_bytes(candidate.image_url)
            _, metrics = inspect_image(raw)
            candidate.width = int(metrics["width"])
            candidate.height = int(metrics["height"])
            candidate.white_ratio = metrics["white_ratio"]
            candidate.sharpness = metrics["sharpness"]
            candidate.quality_score = metrics["quality_score"]
            candidate.text_score = textual_score(candidate, product, quantity)
            if candidate.width < SETTINGS.min_width or candidate.height < SETTINGS.min_height:
                continue
            if candidate.white_ratio < SETTINGS.min_white_ratio:
                continue
            ai = ai_verify(raw, product, quantity)
            candidate.ai_score = ai["score"]
            candidate.language = ai["language"]
            candidate.reason = ai["reason"]
            if best is None or candidate.final_score > best[0].final_score:
                best = (candidate, raw)
        except Exception:
            continue
    return best if best else (None, None)


def save_image(raw: bytes, destination: Path) -> None:
    image = Image.open(BytesIO(raw)).convert("RGB")
    canvas = Image.new("RGB", image.size, "white")
    canvas.paste(image)
    canvas.save(destination, "JPEG", quality=94, optimize=True)


def process(input_path: Path, limit: int | None = None, start: int = 0) -> None:
    SETTINGS.ensure_dirs()
    products = load_products(input_path)
    if start:
        products = products.iloc[start:]
    if limit:
        products = products.head(limit)
    checkpoint = SETTINGS.cache_dir / "results.jsonl"
    done: set[str] = set()
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done.add(row["key"])
            except Exception:
                pass
    records: list[dict[str, Any]] = []
    if checkpoint.exists():
        records = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines() if line.strip()]

    for _, row in tqdm(products.iterrows(), total=len(products), desc="Finding product images"):
        product, quantity = row["Product Name"], row["Quantity"]
        key = hashlib.sha1(f"{product}|{quantity}".lower().encode()).hexdigest()
        if key in done:
            continue
        candidate, raw = choose_best(product, quantity)
        record: dict[str, Any] = {"key": key, "Product Name": product, "Quantity": quantity}
        if candidate and raw:
            filename = f"{slugify(product)}_{slugify(quantity)}.jpg"
            save_image(raw, SETTINGS.images_dir / filename)
            record.update({"Status": "matched", "Image Filename": filename, "Image URL": candidate.image_url, "Source Page": candidate.page_url, "Score": candidate.final_score, "Language": candidate.language, "Width": candidate.width, "Height": candidate.height, "White Ratio": round(candidate.white_ratio, 3), "Reason": candidate.reason})
        else:
            record.update({"Status": "manual_review", "Image Filename": "", "Image URL": "", "Source Page": "", "Score": 0, "Language": "unknown", "Reason": "No candidate passed the configured checks"})
        with checkpoint.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        records.append(record)
        done.add(key)
        time.sleep(SETTINGS.sleep_between_products)

    report = pd.DataFrame(records).drop(columns=["key"], errors="ignore")
    report.to_excel(SETTINGS.reports_dir / "product_image_results.xlsx", index=False)
    report.to_csv(SETTINGS.reports_dir / "product_image_results.csv", index=False)
    report[report["Status"] != "matched"].to_excel(SETTINGS.reports_dir / "manual_review.xlsx", index=False)
    print(f"Finished. Images: {SETTINGS.images_dir}")
    print(f"Report: {SETTINGS.reports_dir / 'product_image_results.xlsx'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find clean product images from an Excel or CSV catalogue.")
    parser.add_argument("input", type=Path, help="Path to .xlsx, .xls or .csv input")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows")
    parser.add_argument("--start", type=int, default=0, help="Start at this zero-based row")
    args = parser.parse_args()
    process(args.input, args.limit, args.start)


if __name__ == "__main__":
    main()
