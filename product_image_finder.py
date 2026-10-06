from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
import unicodedata
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageFile
from rapidfuzz.fuzz import token_set_ratio
from tqdm import tqdm

from config import SETTINGS

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 20_000_000
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


def normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def find_columns(df: pd.DataFrame) -> tuple[str, str]:
    normalized = {normalize_header(column): column for column in df.columns}

    name_exact = ("productname", "producttitle", "itemname", "itemtitle", "productdescription", "itemdescription", "description", "title", "name", "product", "item")
    quantity_exact = (
        "quantitypacksize",
        "quantitysize",
        "packquantity",
        "productquantity",
        "productsize",
        "quantity",
        "packsize",
        "size",
        "weight",
        "qty",
        "pack",
        "volume",
    )

    name_col = next((normalized[key] for key in name_exact if key in normalized), None)
    qty_col = next((normalized[key] for key in quantity_exact if key in normalized), None)

    if name_col is None:
        name_col = next(
            (
                original
                for header, original in normalized.items()
                if ("product" in header or "item" in header) and ("name" in header or "title" in header)
            ),
            None,
        )

    if qty_col is None:
        qty_col = next(
            (
                original
                for header, original in normalized.items()
                if any(token in header for token in ("quantity", "qty", "packsize", "weight", "volume", "size"))
            ),
            None,
        )

    if not name_col or not qty_col:
        raise ValueError(
            "Could not identify product and quantity columns. "
            f"Found: {list(df.columns)}. Expected names similar to Product Title and Quantity / Pack Size."
        )
    return name_col, qty_col


def load_products(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path.resolve()}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # A catalogue may start with a title or notes before its actual header.
        # Reading records separately also tolerates a short title record in CSV.
        import csv
        with path.open(encoding="utf-8-sig", newline="") as source:
            raw_sheets = [pd.DataFrame(list(csv.reader(source)))]
    elif suffix in {".xlsx", ".xls"}:
        raw_sheets = pd.read_excel(path, sheet_name=None, header=None).values()
    else:
        raise ValueError("Input must be .xlsx, .xls or .csv")
    df = None
    for raw in raw_sheets:
        for header_row in range(min(len(raw), 50)):
            candidate = raw.iloc[header_row + 1:].copy()
            candidate.columns = [clean_text(value) for value in raw.iloc[header_row]]
            # Ignore empty/duplicate headings rather than returning ambiguous
            # selections from pandas when a merged title spans columns.
            candidate = candidate.loc[:, (candidate.columns != "") & ~candidate.columns.duplicated()]
            try:
                name_col, qty_col = find_columns(candidate)
            except ValueError:
                continue
            if not candidate[name_col].map(clean_text).ne("").any():
                continue
            df = candidate
            break
        if df is not None:
            break
    if df is None:
        raise ValueError(
            "Could not identify a product table in the first 50 rows of any sheet. "
            "Include headers such as Product Name / Item Description and Quantity / Pack Size."
        )
    out = df[[name_col, qty_col]].copy()
    out.columns = ["Product Name", "Quantity"]
    out["Product Name"] = out["Product Name"].map(clean_text)
    out["Quantity"] = out["Quantity"].map(clean_text)
    out = out[out["Product Name"] != ""].drop_duplicates().reset_index(drop=True)
    print(f"Using columns: product='{name_col}', quantity='{qty_col}'")
    print(f"Loaded {len(out)} unique product rows from {path.name}")
    return out


def ddg_search(query: str, limit: int) -> list[Candidate]:
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get("https://duckduckgo.com/", params={"q": query}, timeout=SETTINGS.request_timeout)
    response.raise_for_status()
    html = response.text
    match = re.search(r'vqd=["\']?([\d-]+)', html)
    if not match:
        raise SearchUnavailable('Web image search did not return a search token.')
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


class SearchUnavailable(RuntimeError):
    pass


# Public product catalogues: no paid search API or customer credentials.
CATALOGUE_SOURCES = (
    'https://swadindia.in', 'https://www.kwfood.co.uk',
    'https://damasgate.store', 'https://superdokan.com/en-lb',
)


def product_tokens(value: str) -> set[str]:
    value = unicodedata.normalize('NFKD', value).casefold()
    value = re.sub(r'\b(?:\d+(?:\.\d+)?)\s*(?:kg|g|ml|l|ltr|litre|liter)\b', ' ', value)
    return {('sauce' if t == 'sauces' else t) for t in re.findall(r'[a-z]+', value)
            if t not in {'the', 'and', 'with', 'of', 'product'}}


def pack_sizes(value: str) -> set[tuple[float, str]]:
    sizes = set()
    for number, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(kg|ml|ltr|litre|liter|g|l)\b', value.casefold()):
        factor = 1000 if unit in ('kg', 'l', 'ltr', 'litre', 'liter') else 1
        sizes.add((float(number) * factor, 'g' if unit in ('g', 'kg') else 'ml'))
    return sizes


def catalogue_search(source: str, product: str, quantity: str) -> list[Candidate]:
    response = requests.get(source + '/search/suggest.json', params={
        'q': product, 'resources[type]': 'product', 'resources[limit]': 10,
        'resources[options][fields]': 'title,variants.title',
    }, headers=HEADERS, timeout=SETTINGS.request_timeout)
    response.raise_for_status()
    rows = response.json()['resources']['results']['products']
    wanted = product_tokens(product)
    sizes = pack_sizes(quantity)
    candidates = []
    for row in rows:
        title = row.get('title', '')
        # Predictive search also returns unrelated recommendations. Never accept them.
        if not wanted or not wanted.issubset(product_tokens(title)):
            continue
        image = row.get('image')
        if isinstance(image, dict): image = image.get('url') or image.get('src')
        if not isinstance(image, str) or not image: continue
        offered = pack_sizes(title)
        reason = ''
        if sizes and not sizes.intersection(offered):
            reason = (f'Pack size differs: source lists {title}; your catalogue requests {quantity}.'
                      if offered else f'Source pack size is unspecified. Check against {quantity}.')
        candidates.append(Candidate(image_url=urljoin(source + '/', image),
            page_url=urljoin(source + '/', row.get('url', '')), title=title,
            source=urlsplit(source).hostname or 'catalogue', reason=reason))
    return candidates


def search_candidates(product: str, quantity: str) -> list[Candidate]:
    results = []
    successful = 0
    with ThreadPoolExecutor(max_workers=len(CATALOGUE_SOURCES)) as pool:
        futures = [pool.submit(catalogue_search, source, product, quantity) for source in CATALOGUE_SOURCES]
        for future in futures:
            try:
                results.extend(future.result()); successful += 1
            except (requests.RequestException, ValueError, KeyError, TypeError):
                continue
    if not results:
        try:
            results = ddg_search(f'{product} {quantity} product', max(8, SETTINGS.max_candidates * 3))
            successful += 1
        except (requests.RequestException, ValueError, SearchUnavailable):
            if not successful:
                raise SearchUnavailable('Image search providers are unavailable or blocking requests. Retry later or upload an image.')
            raise SearchUnavailable('No matching image in the supported retailer catalogues, and web image search is blocked or unavailable. Try a more specific product name or upload an image.')
    results.sort(key=lambda c: (bool(c.reason), len(product_tokens(c.title) - product_tokens(product))))
    seen = set()
    unique = []
    for candidate in results:
        if candidate.image_url not in seen:
            seen.add(candidate.image_url); unique.append(candidate)
    return unique[:min(12, max(8, SETTINGS.max_candidates * 3))]


def validate_image_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Use a public HTTP image URL')
    if parts.port not in (None, 80, 443): raise ValueError('Unsupported image port')
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Private network image URLs are not allowed')


def download_bytes(url: str) -> bytes:
    for _ in range(5):
        validate_image_url(url)
        with requests.get(url, headers=HEADERS, timeout=SETTINGS.request_timeout, allow_redirects=False, stream=True) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get('location', ''))
                continue
            response.raise_for_status()
            if 'image' not in response.headers.get('content-type', '').lower():
                raise ValueError('URL did not return an image')
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 15_000_000: raise ValueError('Image is too large')
            return bytes(raw)
    raise ValueError('Too many image redirects')


def inspect_image(raw: bytes) -> tuple[Image.Image, dict[str, float]]:
    image = Image.open(BytesIO(raw))
    if image.width * image.height > 20_000_000: raise ValueError("Image exceeds 20 megapixels")
    image = image.convert("RGB")
    width, height = image.size
    arr = np.asarray(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    border = max(1, min(width, height) // 12)
    border_pixels = np.concatenate([
        arr[:border].reshape(-1, 3),
        arr[-border:].reshape(-1, 3),
        arr[:, :border].reshape(-1, 3),
        arr[:, -border:].reshape(-1, 3),
    ])
    white_ratio = float(np.mean(np.all(border_pixels >= 238, axis=1)))
    resolution_score = min(100.0, ((width * height) / (1200 * 1200)) * 100)
    sharpness_score = min(100.0, sharpness / 4)
    white_score = min(100.0, white_ratio * 125)
    quality_score = resolution_score * 0.35 + sharpness_score * 0.25 + white_score * 0.40
    return image, {
        "width": width,
        "height": height,
        "sharpness": sharpness,
        "white_ratio": white_ratio,
        "quality_score": quality_score,
    }


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
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"},
                    ],
                }
            ],
        )
        text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(text)
        return {
            "score": float(data.get("score", 0)),
            "language": data.get("language", "unknown"),
            "reason": data.get("reason", ""),
        }
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
    image = Image.open(BytesIO(raw))
    if image.width * image.height > 20_000_000: raise ValueError("Image exceeds 20 megapixels")
    image = image.convert("RGB")
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
            record.update(
                {
                    "Status": "matched",
                    "Image Filename": filename,
                    "Image URL": candidate.image_url,
                    "Source Page": candidate.page_url,
                    "Score": candidate.final_score,
                    "Language": candidate.language,
                    "Width": candidate.width,
                    "Height": candidate.height,
                    "White Ratio": round(candidate.white_ratio, 3),
                    "Reason": candidate.reason,
                }
            )
        else:
            record.update(
                {
                    "Status": "manual_review",
                    "Image Filename": "",
                    "Image URL": "",
                    "Source Page": "",
                    "Score": 0,
                    "Language": "unknown",
                    "Reason": "No candidate passed the configured checks",
                }
            )

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
