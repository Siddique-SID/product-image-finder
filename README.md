# Product Image Finder

A Python script that reads a product list from Excel or CSV, searches for candidate product images, filters for clean high-resolution packshots, optionally verifies the exact product and quantity with AI vision, downloads the best image, and produces Excel/CSV reports.

## What it checks

- Product name relevance
- Exact quantity/pack-size evidence in search metadata
- Minimum image dimensions
- Sharpness
- Mostly white border/background
- English packaging preference through the search queries
- Arabic or Urdu packaging as fallbacks
- Optional AI vision verification for product, quantity, language and catalogue suitability

The script does not blindly accept the first result. Low-quality or non-white-background candidates are rejected and unresolved products are added to `manual_review.xlsx`.

## Input format

Both `.xlsx` and `.csv` are supported. The script automatically recognises common column names.

Example:

| Product Name | Quantity |
|---|---|
| Sofra Barbecue Sauce | 700g |
| Shan Biryani Masala | 50g |

Recognised product columns include `Product Name`, `Product Title`, `Name`, `Title`, and `Product`.

Recognised quantity columns include `Quantity`, `Pack Size`, `Size`, `Weight`, and `Qty`.

## Installation on macOS

```bash
git clone https://github.com/Siddique-SID/product-image-finder.git
cd product-image-finder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p input output/images output/reports output/cache
```

Place your file inside `input/`, for example:

```text
input/missing_image_products.xlsx
```

## Test with 10 products first

```bash
python product_image_finder.py input/missing_image_products.xlsx --limit 10
```

After checking the results, run the complete file:

```bash
python product_image_finder.py input/missing_image_products.xlsx
```

To begin from a later row:

```bash
python product_image_finder.py input/missing_image_products.xlsx --start 200
```

## Output

```text
output/
├── images/
│   ├── Sofra_Barbecue_Sauce_700g.jpg
│   └── Shan_Biryani_Masala_50g.jpg
├── reports/
│   ├── product_image_results.xlsx
│   ├── product_image_results.csv
│   └── manual_review.xlsx
└── cache/
    └── results.jsonl
```

The cache allows the script to resume. Products already recorded in `results.jsonl` are skipped on later runs.

## Optional AI verification

The free/default mode uses search metadata and image-quality rules. For stronger checking of the exact product, quantity, packaging language, background and variant, add an OpenAI API key to `.env`:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_VISION_MODEL=gpt-4.1-mini
```

An API key is optional. Leave it blank to run without AI verification.

## Configuration

Edit `.env`:

```env
MAX_CANDIDATES=8
MIN_WIDTH=500
MIN_HEIGHT=500
MIN_WHITE_RATIO=0.45
REQUEST_TIMEOUT=20
SLEEP_BETWEEN_PRODUCTS=1.2
```

- Increase `MAX_CANDIDATES` when products are difficult to locate.
- Reduce `MIN_WHITE_RATIO` slightly if valid images are being rejected.
- Keep a delay between products to reduce blocking by search providers.

## Important accuracy note

Automated search can still return old packaging, regional variants, multipacks or the wrong size. Review the report, especially low-score matches. For commercial catalogue use, confirm that you have permission to use the chosen images and retain the source URLs recorded in the report.

## Reset and rerun

Delete the cache and existing output:

```bash
rm -f output/cache/results.jsonl
rm -f output/images/*
rm -f output/reports/*
```

Then run the script again.

## PWA studio — by Siddique Sayed

The `feat/product-finder-pwa` branch adds a React + TypeScript customer interface and a FastAPI adapter around the existing search and image-quality functions. The original CLI remains available.

### Run locally

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend
npm ci
npm run build
cd ..
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000. For frontend development, run `npm run dev` in `frontend/` with the backend still running.

1. Upload a CSV/XLSX with product name and quantity columns (up to 500 products / 10 MB).
2. Check the detected catalogue before selecting **Find images**.
3. Review candidates, their resolution, white border estimate and source page. Approve, reject or upload a replacement.
4. Export CSV/XLSX reports or a ZIP containing only approved images.

The customer beta includes owner access, single-use customer invitations, individual accounts, private catalogue libraries and one-time recovery codes. Jobs and images use PostgreSQL when `DATABASE_URL` is configured; local development uses SQLite. Searches run sequentially with pause/resume and retries. CSV, XLSX and approved-image ZIP exports are available, with manual image replacement when automatic search cannot find a match.

The installable PWA caches its interface only. Private catalogues and images require an internet connection and are never stored in the service-worker cache. Install through Chrome/Edge/Android, or Safari → Share → Add to Home Screen on iPhone.

### Hosted deployment (Render)

The Dockerfile serves the frontend and Python API together. The committed Blueprint keeps the free plan and `REQUIRE_AUTH=true`. Preserve `APP_PASSWORD` and `SESSION_SECRET`. Add an external PostgreSQL `DATABASE_URL` for persistent customer accounts and uploads; without it, the app displays a demo warning because Render Free's filesystem is temporary. No paid services are provisioned by this change.

See [DEPLOYMENT.md](DEPLOYMENT.md) for customer invitations, storage setup, limitations and verification. Automatic matching is an estimate and can be blocked by the search provider. Confirm exact variants, pack sizes and image usage rights before publishing. The web app makes no AI API calls and includes no subscription billing or store-publishing integration.
