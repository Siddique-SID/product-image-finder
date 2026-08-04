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
