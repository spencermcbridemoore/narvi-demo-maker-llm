"""Persistence: problem banks and runs in memory, mirrored to JSON files; a SQLite
cache of raw model samples so re-runs are free and interrupted runs resume.

Everything lives under ``GRADER_DATA_DIR`` (default ``backend/app/data/grader``),
which is git-ignored and, in Docker, sits on the backend's data volume.

What each delete removes:
  * ``delete_run``: that run's file (its grades and votes). The bank and the cached
    samples stay.
  * ``delete_assessment`` + ``SampleCache.delete_bank``: the bank (problem text,
    rubric, responses) and the cached samples tagged with it. The API also removes
    the bank's runs.
  * ``SampleCache.clear``: every cached sample, including rows cached before samples
    were tagged with a bank.
Nothing here is logged.

A bank is written to disk before the in-memory copy is replaced, so a save that
fails leaves memory and disk agreeing on the last good state.
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


# --- problem banks -----------------------------------------------------------

_ASSESSMENTS: dict[str, Assessment] = {}


def save_assessment(a: Assessment) -> Assessment:
    path = data_dir() / "assessments" / f"{_safe(a.id)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(a.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)
    _ASSESSMENTS[a.id] = a      # only once the file is written
    return a


def get_assessment(aid: str) -> Assessment:
    if aid not in _ASSESSMENTS:
        path = data_dir() / "assessments" / f"{_safe(aid)}.json"
        if not path.is_file():
            raise KeyError(aid)
        a = Assessment.model_validate_json(path.read_text(encoding="utf-8"))
        a.id = aid      # the file name is the id
        _ASSESSMENTS[aid] = a
    return _ASSESSMENTS[aid]


def list_assessments() -> list[tuple[str, Assessment | None, float]]:
    """Every saved bank file: its id, the bank, and the file's modified time. A file
    that cannot be read is not fatal: it is listed without a bank, so that it can
    still be deleted."""
    out: list[tuple[str, Assessment | None, float]] = []
    for path in (data_dir() / "assessments").glob("*.json"):
        try:
            bank = get_assessment(path.stem)
        except Exception:
            bank = None
        out.append((path.stem, bank, path.stat().st_mtime))
    return out


def has_assessment_file(aid: str) -> bool:
    """Is there a saved file for this id, readable or not?"""
    try:
        return (data_dir() / "assessments" / f"{_safe(aid)}.json").is_file()
    except KeyError:
        return False


def delete_assessment(aid: str) -> None:
    _ASSESSMENTS.pop(aid, None)
    (data_dir() / "assessments" / f"{_safe(aid)}.json").unlink(missing_ok=True)


# --- runs (stored as plain dicts; runner.Run does the typing) ------------------

_RUN_INDEX: dict[str, tuple[float, dict]] = {}   # run id -> (file mtime, the run without its cells)


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


def run_header(run_id: str) -> dict:
    """A saved run without its cells, read without building the whole run in memory
    (a full run is about a megabyte). Used to list runs and to find a bank's runs."""
    path = data_dir() / "runs" / f"{_safe(run_id)}.json"
    if not path.is_file():
        raise KeyError(run_id)
    mtime = path.stat().st_mtime
    hit = _RUN_INDEX.get(run_id)
    if hit is None or hit[0] != mtime:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(run_id)
        data.pop("cells", None)
        hit = _RUN_INDEX[run_id] = (mtime, data)
    return dict(hit[1])


def delete_run(run_id: str) -> None:
    _RUN_INDEX.pop(run_id, None)
    (data_dir() / "runs" / f"{_safe(run_id)}.json").unlink(missing_ok=True)


# --- sample cache ------------------------------------------------------------

class SampleCache:
    """key -> JSON sample. Keys hash (model, effort, prompt version, messages, sample #).

    Each sample is tagged with the bank it was first cached for, so deleting a bank
    can take its samples with it. The tags live in a table of their own
    (``sample_banks``): the ``samples`` table keeps the shape the MVP wrote, so the
    existing file needs no migration and an older build can still use it. Samples
    cached before tagging existed have no tag and go only with ``clear``.
    """

    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS samples (key TEXT PRIMARY KEY, value TEXT, created REAL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sample_banks (key TEXT PRIMARY KEY, bank TEXT NOT NULL)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS sample_banks_bank ON sample_banks (bank)")
        self._db.commit()
        self.generation = 0     # goes up with every clear()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM samples WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict, bank: str | None = None,
            generation: int | None = None) -> None:
        """Store a sample. A caller that started before the cache was last cleared
        passes the ``generation`` it started with, and its late answer is dropped:
        a cleared cache stays cleared."""
        with self._lock:
            if generation is not None and generation != self.generation:
                return
            self._db.execute(
                "INSERT OR REPLACE INTO samples (key, value, created) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            if bank:
                self._db.execute(
                    "INSERT OR REPLACE INTO sample_banks (key, bank) VALUES (?, ?)", (key, bank))
            self._db.commit()

    def count(self, bank: str | None = None) -> int:
        """Samples tagged with ``bank``, or every sample when no bank is given."""
        with self._lock:
            if bank is None:
                row = self._db.execute("SELECT COUNT(*) FROM samples").fetchone()
            else:
                row = self._db.execute(
                    "SELECT COUNT(*) FROM samples JOIN sample_banks USING (key) WHERE bank = ?",
                    (bank,)).fetchone()
        return int(row[0])

    def delete_bank(self, bank: str) -> int:
        with self._lock:
            n = self._db.execute(
                "DELETE FROM samples WHERE key IN (SELECT key FROM sample_banks WHERE bank = ?)",
                (bank,)).rowcount
            self._db.execute("DELETE FROM sample_banks WHERE bank = ?", (bank,))
            self._db.commit()
        return n

    def clear(self) -> int:
        with self._lock:
            self.generation += 1
            n = self._db.execute("DELETE FROM samples").rowcount
            self._db.execute("DELETE FROM sample_banks")
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
    _RUN_INDEX.clear()
    if _CACHE is not None:
        _CACHE._db.close()
    _CACHE = None
