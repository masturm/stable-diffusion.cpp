from pathlib import Path

from config import MODEL_DIRS
from models import ModelInfo

CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("high_noise", ["highnoise", "high_noise", "high-noise"]),
    ("low_noise", ["lownoise", "low_noise", "low-noise"]),
    ("diffusion", ["t2v", "i2v", "diffusion", "unet", "dit"]),
    ("vae", ["vae", "taesd", "tae"]),
    ("text_encoder", ["t5xxl", "t5", "clip", "text_encoder", "umt5"]),
]


def classify_model(filename: str) -> str:
    lower = filename.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return category
    return "unknown"


def scan_models() -> list[ModelInfo]:
    results: list[ModelInfo] = []
    seen: set[str] = set()
    for directory in MODEL_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".gguf", ".safetensors"):
                continue
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            size_mb = path.stat().st_size / (1024 * 1024)
            results.append(
                ModelInfo(
                    path=resolved,
                    name=path.stem,
                    size_mb=round(size_mb, 1),
                    category=classify_model(path.name),
                )
            )
    return results
