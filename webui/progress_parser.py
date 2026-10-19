import re

PROGRESS_RE = re.compile(r"\r?\[.*?\]\s+(\d+)/(\d+)\s+-\s+(.+)")
SAVE_VIDEO_RE = re.compile(r"save result video to '(.+)'")
SAVE_IMAGE_RE = re.compile(r"save result image.*to '(.+)'")
LOADING_RE = re.compile(r"(loading|building|initializing)", re.IGNORECASE)


def parse_progress_line(line: str) -> tuple[int, int, str] | None:
    m = PROGRESS_RE.search(line)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3).strip()
    return None


def parse_output_path(line: str) -> str | None:
    m = SAVE_VIDEO_RE.search(line) or SAVE_IMAGE_RE.search(line)
    if m:
        return m.group(1)
    return None


def detect_phase(line: str) -> str | None:
    lower = line.lower()
    if "loading" in lower or "building" in lower:
        return "loading"
    if "sampling" in lower or "step" in lower:
        return "sampling"
    if "decoding" in lower or "vae" in lower:
        return "decoding"
    if "save result" in lower:
        return "saving"
    if "unloading" in lower:
        return "unloading"
    return None
