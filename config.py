from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    root: Path = Path(__file__).resolve().parent
    input_dir: Path = root / "input"
    output_dir: Path = root / "output"
    images_dir: Path = output_dir / "images"
    reports_dir: Path = output_dir / "reports"
    cache_dir: Path = output_dir / "cache"
    max_candidates: int = int(os.getenv("MAX_CANDIDATES", "8"))
    min_width: int = int(os.getenv("MIN_WIDTH", "500"))
    min_height: int = int(os.getenv("MIN_HEIGHT", "500"))
    min_white_ratio: float = float(os.getenv("MIN_WHITE_RATIO", "0.45"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "20"))
    sleep_between_products: float = float(os.getenv("SLEEP_BETWEEN_PRODUCTS", "1.2"))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_vision_model: str = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")

    def ensure_dirs(self) -> None:
        for path in (self.input_dir, self.images_dir, self.reports_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()
