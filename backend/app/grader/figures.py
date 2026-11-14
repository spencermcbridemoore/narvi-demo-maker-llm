"""Figures shown with a problem, told to the grader in words.

An export may name a figure per row (a ``Problem_figure`` column). The grading model
reads text, so each figure needs a written description, which is added to the problem
text of every version that shows it. A description comes from, in this order:

  1. a text file the teacher put next to the image, same name with ``.txt``
     (``ramp_push.png`` -> ``ramp_push.txt``);
  2. ``figure_descriptions.yaml`` in the run's output folder, an entry marked
     ``status: approved``.

``draft`` writes AI descriptions into that YAML marked ``status: draft``. Like every AI
draft in the grader they are proposals: nothing is graded with a draft until the teacher
reads it, edits it if needed, and changes its status to ``approved``.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path

import yaml

from ..llm.factory import Role, get_model

DESCRIPTIONS = "figure_descriptions.yaml"
_PROMPT = (Path(__file__).parent / "prompts" / "figure_describe.txt").read_text(encoding="utf-8").strip()
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def locate(question_dir: Path, names: set[str]) -> dict[str, Path]:
    """Each named figure file, found anywhere under the question folder."""
    found: dict[str, Path] = {}
    for path in sorted(question_dir.rglob("*")):
        if path.is_file() and path.name in names and path.name not in found:
            found[path.name] = path
    return found


def _read_yaml(run_dir: Path) -> list[dict]:
    path = run_dir / DESCRIPTIONS
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [e for e in data.get("figures") or [] if isinstance(e, dict) and e.get("file")]


def descriptions(files: dict[str, Path], run_dir: Path) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """({figure: (description, where it came from)}, [figures still waiting as drafts])."""
    out: dict[str, tuple[str, str]] = {}
    for name, path in files.items():
        side = path.with_suffix(".txt")
        if side.exists() and side.read_text(encoding="utf-8").strip():
            out[name] = (side.read_text(encoding="utf-8").strip(), f"teacher file {side.name}")
    drafts: list[str] = []
    for entry in _read_yaml(run_dir):
        name = str(entry["file"])
        if name not in files or name in out:
            continue
        text = str(entry.get("description") or "").strip()
        if str(entry.get("status", "")).strip().lower() == "approved" and text:
            out[name] = (text, f"approved in {DESCRIPTIONS}")
        else:
            drafts.append(name)
    return out, drafts


class _Block(str):
    pass


yaml.SafeDumper.add_representer(
    _Block, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", str(s), style="|"))


def _write_yaml(run_dir: Path, entries: list[dict]) -> Path:
    path = run_dir / DESCRIPTIONS
    head = ("# Figure descriptions for the grader. Read each one against its image, edit it if\n"
            "# needed, then change its status from draft to approved. Only approved ones are used.\n")
    body = yaml.safe_dump(
        {"figures": [{**e, "description": _Block(str(e.get("description", "")).strip() + "\n")} for e in entries]},
        sort_keys=False, allow_unicode=True, width=100)
    path.write_text(head + body, encoding="utf-8")
    return path


async def _describe(path: Path, problem_text: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    model = get_model(Role.RUBRIC)
    if getattr(model, "reasoning_effort", None) is not None:
        model = model.model_copy(update={"reasoning_effort": "low"})
    message = HumanMessage(content=[
        {"type": "text", "text": f"One version of the problem this diagram is shown with:\n<problem>\n"
                                 f"{problem_text.strip()}\n</problem>\n\nDescribe the diagram."},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
    ])
    out = await model.ainvoke([SystemMessage(_PROMPT), message])
    content = out.content
    if isinstance(content, list):
        content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content).strip()


def draft(files: dict[str, Path], problem_for: dict[str, str], run_dir: Path) -> tuple[Path, int]:
    """AI descriptions for figures that have none yet, written as drafts. Entries
    already in the file (drafts or approved) are kept as they are."""
    have, _ = descriptions(files, run_dir)
    entries = _read_yaml(run_dir)
    listed = {str(e["file"]) for e in entries}
    todo = [n for n in sorted(files) if n not in have and n not in listed]

    async def all_of() -> list[str]:
        return await asyncio.gather(*(_describe(files[n], problem_for.get(n, "")) for n in todo))

    texts = asyncio.run(all_of()) if todo else []
    model = get_model(Role.RUBRIC)
    name = getattr(model, "deployment_name", None) or getattr(model, "model_name", None) or "model"
    for n, text in zip(todo, texts):
        entries.append({"file": n, "status": "draft", "drafted_by": str(name), "description": text})
    run_dir.mkdir(parents=True, exist_ok=True)
    return _write_yaml(run_dir, entries), len(todo)


def with_figure(problem_text: str, description: str) -> str:
    """The problem text as the grader is told it when a figure goes with it."""
    return (f"{problem_text.rstrip()}\n\nFIGURE (a diagram shown with the problem, described in words):\n"
            f"{description.strip()}")
