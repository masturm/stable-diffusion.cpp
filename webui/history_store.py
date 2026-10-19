import json
from pathlib import Path

from config import HISTORY_DIR
from models import Job


def save_job(job: Job):
    path = HISTORY_DIR / f"{job.id}.json"
    path.write_text(job.model_dump_json(indent=2), encoding="utf-8")


def load_job(job_id: str) -> Job | None:
    path = HISTORY_DIR / f"{job_id}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Job(**data)


def list_history() -> list[Job]:
    jobs: list[Job] = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            jobs.append(Job(**data))
        except Exception:
            continue
    return jobs
