"""Score a run with the kit's own ``tools/evaluate.py`` when reference labels exist.

The kit's scorer is loaded from the kit folder unchanged, so the numbers match
what BRIEF.md asks for (per-item F1/kappa vs each human grader, exact-vector match,
human ceiling, confidence AUROC). Needs pandas; only used for the Chen example.
"""

from __future__ import annotations

import importlib.util
import math
from functools import lru_cache

from .adapters import kit_dir
from .model import Assessment


@lru_cache(maxsize=1)
def _kit_evaluate():
    kit = kit_dir()
    if kit is None:
        return None
    spec = importlib.util.spec_from_file_location("grader_kit_evaluate", kit / "tools" / "evaluate.py")
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clean(x):
    """JSON-safe: numpy scalars -> Python, NaN/inf -> None."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and callable(x.item):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def evaluate_run(run, assessment: Assessment) -> dict | None:
    if not assessment.source.startswith("example:chen"):
        return None
    mod = _kit_evaluate()
    if mod is None:
        return None
    rows = [
        {"instance_id": c.instance_id, "pred": c.final, "confidence": c.confidence}
        for c in run.cells.values() if c.final is not None
    ]
    if not rows:
        return None
    import pandas as pd

    inst = pd.read_csv(kit_dir() / "data" / "instances.csv", keep_default_na=False,
                       dtype={"label_agreed": str})
    result = mod.evaluate(pd.DataFrame(rows), inst, "this run")
    ceiling = mod.human_ceiling(inst)[0]
    return _clean({"run": result, "human": ceiling})
