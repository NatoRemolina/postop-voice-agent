import json
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

_lock = threading.Lock()


def _path(name: str) -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / name


def append_jsonl(name: str, record: dict[str, Any]) -> None:
    record = {"ts": time.time(), **record}
    with _lock:
        with open(_path(name), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(name: str) -> list[dict]:
    path = _path(name)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
