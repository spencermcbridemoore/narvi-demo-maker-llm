"""Offline tests for the folder workflow (app.grader.batch). Made-up data only; the model
call is faked. The end-to-end tests need Node and the frontend's happy-dom (the answers
are prepared by the web app's own code) and skip without them."""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from app.grader import batch, grade, runner, store
from app.grader.adapters import read_rubric_yaml
from app.grader.agreement import stats
from app.grader.model import Sample

EXPORT = (
    "ID,ItemID,ProblemBody,Problem_figure,EarnedPoints,Explanation,rubric_1,rubric_2\n"
    'aaa111,101,A box is pushed up a ramp.,ramp_a.png,0.0,"<p>Forces balance: '
    '<img class=""equation_image"" data-equation-content=""F\\cos\\theta - f = 0""> so it is less dense</p>",1,1\n'
    "bbb222,101,A box is pushed up a ramp.,ramp_a.png,0.0,NA,0,0\n"
    'ccc333,102,A box is pulled down a ramp.,ramp_b.png,2.0,"<p>I used energy.</p>",,\n'
    'ddd444,102,A box is pulled down a ramp.,ramp_b.png,0.0,"<p>It is less dense.</p>",1,0\n'
)
RUBRIC = """# made up
judgment: binary
rubrics:
  - id: 1
    item: Says the forces balance
    specifications:
      - It must
        - name a force
        - say that they balance
      - The change Kf - Ki is not needed
  - id: 2
    item: Writes an equation
"""

needs_node = pytest.mark.skipif(
    shutil.which("node") is None or not (batch.REPO_ROOT / "frontend" / "node_modules" / "happy-dom").exists(),
    reason="needs Node and the frontend's node_modules")


@pytest.fixture(autouse=True)
def _grader_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADER_DATA_DIR", str(tmp_path / "grader"))
    store.reset_for_tests()
    runner._RUNS.clear()
    yield
    store.reset_for_tests()
    runner._RUNS.clear()
    grade._OVERRIDE.clear()
    grade.grading_model.cache_clear()


def _fake(monkeypatch, calls: list):
    async def fake_grade_once(messages, effort, attempts=5):
        calls.append(messages)
        answer = re.search(r"<response>\n(.*?)\n</response>", messages[1][1], re.S).group(1)
        credit = "less dense" in answer
        return Sample(verdict=int(credit), comparison="fake", evidence=["less dense"] if credit else []), {
            "input_tokens": 100, "output_tokens": 20, "cached_tokens": 0, "reasoning_tokens": 0}

    monkeypatch.setattr(runner, "grade_once", fake_grade_once)
    monkeypatch.setattr(runner, "model_name", lambda: "gpt-5-mini")


def _package(tmp_path: Path) -> Path:
    q = tmp_path / "data" / "Pkg" / "Exam A" / "Some question"
    (q / "pics").mkdir(parents=True)
    (tmp_path / "data" / "Pkg" / "README.md").write_text("made up", encoding="utf-8")
    (q / "whatever_export.csv").write_text(EXPORT, encoding="utf-8")
    (q / "my rubric.yml").write_text(RUBRIC, encoding="utf-8")
    (q / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")   # not an export: ignored
    for name in ("ramp_a.png", "ramp_b.png"):
        (q / "pics" / name).write_bytes(b"\x89PNG fake")
    return q


def _rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def test_outline_specifications_keep_their_sub_points():
    items, found = read_rubric_yaml(RUBRIC)
    assert items[0].specifications == ["It must\n  - name a force\n  - say that they balance",
                                       "The change Kf - Ki is not needed"]
    assert "specification_outline_kept" in [f.code for f in found]


def test_agreement_stats():
    s = stats([(1, 1), (1, 0), (0, 0), (0, 0)])
    assert s["n"] == 4 and s["agree"] == 0.75 and s["too_strict"] == 1 and s["too_generous"] == 0
    assert s["kappa"] == pytest.approx(0.5)
    assert stats([(1, 1), (1, 1)])["kappa"] is None
    assert stats([])["n"] == 0


def test_files_are_found_by_content_not_by_name(tmp_path):
    q = _package(tmp_path)
    found = batch.discover(tmp_path / "data")
    assert [f.export.name for f in found] == ["whatever_export.csv"]
    assert found[0].rubric.name == "my rubric.yml" and not found[0].problem
    assert batch.package_of(q).name == "Pkg"
    (q / "second.yaml").write_text(RUBRIC, encoding="utf-8")
    assert "keep one" in batch.discover(q)[0].problem


def test_sample_spreads_across_versions():
    version_of = {0: "a", 1: "a", 2: "a", 3: "b", 4: "c"}
    picked = batch.spread(list(version_of), version_of, 3, seed=7)
    assert len(picked) == 3 and {version_of[i] for i in picked} == {"a", "b", "c"}
    assert picked == batch.spread(list(version_of), version_of, 3, seed=7)


@needs_node
def test_figures_without_an_approved_description_stop_the_question(tmp_path, monkeypatch):
    calls: list = []
    _fake(monkeypatch, calls)
    q = _package(tmp_path)
    out = tmp_path / "out"
    assert batch.main([str(q), "--out", str(out)]) == 0
    assert calls == []                                       # nothing graded
    assert not list(out.rglob("*_ai.csv"))
    # The teacher writes the descriptions next to the images: now it grades, and says so.
    (q / "pics" / "ramp_a.txt").write_text("A ramp; F points up the slope.", encoding="utf-8")
    (q / "pics" / "ramp_b.txt").write_text("A ramp; F points down the slope.", encoding="utf-8")
    assert batch.main([str(q), "--out", str(out)]) == 0
    assert calls and all("FIGURE (a diagram" in m[1][1] for m in calls)
    assert any("F points up the slope" in m[1][1] for m in calls)


@needs_node
def test_a_question_folder_end_to_end(tmp_path, monkeypatch):
    calls: list = []
    _fake(monkeypatch, calls)
    q = _package(tmp_path)
    out = tmp_path / "out"
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--estimate"]) == 0
    assert calls == []                                       # an estimate makes no calls
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--k", "3"]) == 0

    run_dir = out / "Pkg" / "Exam A" / "Some question"
    # 3 answers (the NA row is not sent) x 2 items x 3 votes
    assert len(calls) == 18
    assert all("NA" != re.search(r"<response>\n(.*?)\n</response>", m[1][1], re.S).group(1) for m in calls)
    assert any(r"\(F\cos\theta - f = 0\)" in m[1][1] for m in calls)   # equation kept as LaTeX
    assert all("<p>" not in m[1][1] for m in calls)
    assert all("FIGURE" not in m[1][1] for m in calls)

    rows = _rows(run_dir / "whatever_export_ai.csv")
    assert rows[0] == EXPORT.splitlines()[0].split(",")     # shaped like the input
    by_id = {r[0]: r for r in rows[1:]}
    assert by_id["aaa111"][6:] == ["1", "1"]
    assert by_id["bbb222"][6:] == ["0", "0"]                 # no answer: 0 by rule
    assert by_id["ccc333"][6:] == ["0", "0"]                 # not human-graded, still graded
    assert by_id["ddd444"][6:] == ["1", "1"]

    details = _rows(run_dir / "whatever_export_ai_details.csv")
    head = details[0]
    assert head[:5] == ["ID", "ItemID", "one_time_id", "EarnedPoints", "status"]
    d = {r[0]: dict(zip(head, r)) for r in details[1:]}
    assert d["bbb222"]["status"] == "no_answer" and d["bbb222"]["flags_1"] == "no_answer"
    assert d["ddd444"]["disagrees"] == "2"
    text = (run_dir / "whatever_export_ai_details.csv").read_text(encoding="utf-8")
    assert "less dense" not in text and "energy" not in text  # no answer text in the details

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["no_answer"] == 1 and report["counts"]["selected"] == 3
    assert report["calls"] == 18 and report["cost"] > 0
    a = report["agreement"]
    assert a["all"]["n"] == 4 and a["all"]["too_generous"] == 1 and a["no_answer_rows"]["n"] == 2
    assert "NOT shown" in report["figures"]
    assert "## Agreement with the human grades" in (run_dir / "report.md").read_text(encoding="utf-8")

    # Same settings again: every vote comes from the cache.
    calls.clear()
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--k", "3"]) == 0
    assert calls == []


def test_prices_match_whole_model_names_only():
    assert batch._price("gpt-5-mini", None) == batch.PRICES["gpt-5-mini"]
    assert batch._price("gpt-4.1-mini-2025-04-14", None) == batch.PRICES["gpt-4.1-mini"]
    assert batch._price("gpt-5.4-mini", None) is None       # not priced as gpt-5
    assert batch._price("anything", "1,4") == (1.0, 1.0, 4.0)
    assert batch._price("@vertex-general/gemini-2.5-flash", None) == batch.PRICES["gemini-2.5-flash"]
    assert batch._price("@vertex-general/gemini-3.5-flash", None) is None
    assert [bool(batch.REASONING.match(m)) for m in ("gpt-5-nano", "gpt-5-chat", "gpt-6-luna", "o4-mini",
                                                       "gpt-4.1-nano", "claude-haiku-4-5")] == \
        [True, False, True, True, False, False]


@needs_node
def test_another_model_grades_into_its_own_folder(tmp_path, monkeypatch):
    calls: list = []
    _fake(monkeypatch, calls)
    built: list = []

    class FakeModel:
        def __init__(self, name, reasoning):
            self.deployment_name = name
            self.reasoning_effort = "minimal" if reasoning else None

    def fake_named(name, reasoning, profile=None):
        built.append((name, reasoning) if profile is None else (name, reasoning, profile))
        return FakeModel(name, reasoning)

    monkeypatch.setattr(grade, "get_model_named", fake_named)
    monkeypatch.setattr(runner, "model_name", grade.model_name)      # undo _fake's fixed name
    monkeypatch.setattr(grade, "_OVERRIDE", {})
    q = _package(tmp_path)
    out = tmp_path / "out"
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--k", "1",
                       "--model", "gpt-4.1-nano"]) == 0
    assert built == [("gpt-4.1-nano", False)]
    report = json.loads((out / "Pkg" / "Exam A" / "Some question" / "gpt-4.1-nano" / "report.json")
                        .read_text(encoding="utf-8"))
    assert (report["model"], report["effort"], report["price"]) == ("gpt-4.1-nano", "none", list(batch.PRICES["gpt-4.1-nano"]))
    assert len(calls) == 6
    # The same answers on another model are new calls, not cache hits.
    calls.clear()
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--k", "1", "--model", "o4-mini"]) == 0
    assert built[-1] == ("o4-mini", True) and len(calls) == 6
    assert (out / "Pkg" / "Exam A" / "Some question" / "o4-mini-minimal" / "report.json").exists()
    # A model of another profile (Orion: "@group/model") gets a short, profile-named folder.
    calls.clear()
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--k", "1", "--profile", "orion",
                       "--model", "@vertex-general/gemini-2.5-flash"]) == 0
    assert built[-1] == ("@vertex-general/gemini-2.5-flash", False, "orion") and len(calls) == 6
    report = json.loads((out / "Pkg" / "Exam A" / "Some question" / "orion-gemini-2.5-flash" / "report.json")
                        .read_text(encoding="utf-8"))
    assert report["model"] == "@vertex-general/gemini-2.5-flash" and report["price"] == list(batch.PRICES["gemini-2.5-flash"])


@needs_node
def test_rows_graded_with_a_sample(tmp_path, monkeypatch):
    calls: list = []
    _fake(monkeypatch, calls)
    q = _package(tmp_path)
    out = tmp_path / "out"
    assert batch.main([str(q), "--out", str(out), "--figures", "text", "--rows", "graded",
                       "--sample", "1", "--k", "1"]) == 0
    assert len(calls) == 2                                   # one answer x 2 items x 1 vote
    report = json.loads((out / "Pkg" / "Exam A" / "Some question" / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["selected"] == 1
