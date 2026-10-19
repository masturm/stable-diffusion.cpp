import json
from pathlib import Path

from config import WORKFLOWS_DIR
from models import WorkflowConfig


def list_workflows() -> list[str]:
    return sorted(p.stem for p in WORKFLOWS_DIR.glob("*.json"))


def load_workflow(name: str) -> WorkflowConfig | None:
    path = WORKFLOWS_DIR / f"{name}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkflowConfig(**data)


def save_workflow(name: str, config: WorkflowConfig):
    path = WORKFLOWS_DIR / f"{name}.json"
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")


def delete_workflow(name: str) -> bool:
    path = WORKFLOWS_DIR / f"{name}.json"
    if path.is_file():
        path.unlink()
        return True
    return False
