from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SD_CLI_PATH = REPO_ROOT / "build" / "bin" / "sd-cli"

MODEL_DIRS = [
    REPO_ROOT / "models",
    Path("/media/muselko/WD black 2TB1/TTV"),
]

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"
HISTORY_DIR = Path(__file__).resolve().parent / "history"
OUTPUT_DIR = REPO_ROOT / "webui" / "outputs"
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"

HOST = "0.0.0.0"
PORT = 8080

for d in (WORKFLOWS_DIR, HISTORY_DIR, OUTPUT_DIR, UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)
