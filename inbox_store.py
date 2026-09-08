"""Cross-process inbox. Lock a separate file: JSON itself is atomically replaced."""
import fcntl
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def atomic_write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked_inbox(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("Invalid inbox format; preserving file")
        yield data
        atomic_write(path, data)


def append(path: Path, text: str, source: str):
    with locked_inbox(path) as items:
        items.append({"id": uuid.uuid4().hex, "ts": time.time(), "source": source, "text": text})
        del items[:-50]


def peek(path: Path):
    with locked_inbox(path) as items:
        if not items:
            return None
        # Upgrade legacy records so acknowledgment never removes a newer message.
        items[0].setdefault("id", uuid.uuid4().hex)
        return dict(items[0])


def acknowledge(path: Path, item_id: str):
    with locked_inbox(path) as items:
        items[:] = [item for item in items if item.get("id") != item_id]
