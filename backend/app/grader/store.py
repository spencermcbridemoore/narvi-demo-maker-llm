"""Persistence: assessments and runs in memory, mirrored to JSON files; a SQLite
cache of raw model samples so re-runs are free and interrupted runs resume.

Everything lives under ``GRADER_DATA_DIR`` (default ``backend/app/data/grader``),
which is git-ignored and, in Docker, sits on the backend's data volume.
Retention: ``delete_run`` removes a run's file; cached samples go with
``clear_cache``. Nothing here is logged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..config import DATA_DIR
from .model import Assessment


def data_dir() -> Path:
    path = Path(os.getenv("GRADER_DATA_DIR", str(DATA_DIR / "grader")))
    (path / "assessments").mkdir(parents=True, exist_ok=True)
    (path / "runs").mkdir(parents=True, exist_ok=True)
    return path


def _safe(ident: str) -> str:
    if not ident or not all(ch.isalnum() or ch in "-_" for ch in ident):
        raise KeyError(ident)
    return ident


# --- assessments -------------------------------------------------------------

_ASSESSMENTS: dict[str, Assessment] = {}


def save_assessment(a: Assessment) -> Assessment:
    _ASSESSMENTS[a.id] = a
    (data_dir() / "assessments" / f"{_safe(a.id)}.json").write_text(
        a.model_dump_json(), encoding="utf-8"
    )
    return a


def get_assessment(aid: str) -> Assessment:
    if aid not in _ASSESSMENTS:
        path = data_dir() / "assessments" / f"{_safe(aid)}.json"
        if not path.is_file():
            raise KeyError(aid)
        _ASSESSMENTS[aid] = Assessment.model_validate_json(path.read_text(encoding="utf-8"))
    return _ASSESSMENTS[aid]


# --- runs (stored as plain dicts; runner.Run does the typing) ------------------

def save_run_json(run_id: str, payload: str) -> None:
    path = data_dir() / "runs" / f"{_safe(run_id)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def load_run_json(run_id: str) -> str:
    path = data_dir() / "runs" / f"{_safe(run_id)}.json"
    if not path.is_file():
        raise KeyError(run_id)
    return path.read_text(encoding="utf-8")


def list_run_ids() -> list[str]:
    files = sorted((data_dir() / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in files]


def delete_run(run_id: str) -> None:
    (data_dir() / "runs" / f"{_safe(run_id)}.json").unlink(missing_ok=True)


# --- sample cache ------------------------------------------------------------

class SampleCache:
    """key -> JSON sample. Keys hash (model, effort, prompt version, messages, sample #)."""

    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS samples (key TEXT PRIMARY KEY, value TEXT, created REAL)"
        )
        self._db.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM samples WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO samples VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._db.commit()

    def clear(self) -> int:
        with self._lock:
            n = self._db.execute("DELETE FROM samples").rowcount
            self._db.commit()
            self._db.execute("VACUUM")
        return n


_CACHE: SampleCache | None = None


def cache() -> SampleCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = SampleCache(data_dir() / "sample_cache.sqlite")
    return _CACHE


def reset_for_tests() -> None:
    """Forget in-memory state (tests point GRADER_DATA_DIR at a temp dir)."""
    global _CACHE
    _ASSESSMENTS.clear()
    if _CACHE is not None:
        _CACHE._db.close()
    _CACHE = None
