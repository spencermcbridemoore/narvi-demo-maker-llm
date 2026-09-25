"""Grade the Chen kit from the terminal and score it with the kit's evaluate.py.

    cd backend
    ../.venv/Scripts/python -m app.grader.cli --rubric simple --k 3
    ../.venv/Scripts/python -m app.grader.cli --rubric detailed_latest --problems Q2 --max-responses 5

Same runner and cache as the web app, so numbers here are the app's behavior.
Writes predictions (the kit's CSV format) under GRADER_DATA_DIR/cli/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import time

from . import metrics, runner, store
from .adapters import CHEN_VERSIONS, load_chen
from .grade import EFFORTS


def _f(x) -> str:
    return "  -  " if x is None else f"{x:.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rubric", default="simple", choices=list(CHEN_VERSIONS))
    ap.add_argument("--k", type=int, default=3, help="samples per (response, item); odd avoids ties")
    ap.add_argument("--effort", default="minimal", choices=EFFORTS)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--problems", default="", help="comma-separated problem ids, e.g. Q1,Q3")
    ap.add_argument("--max-responses", type=int, default=None, help="per problem")
    args = ap.parse_args(argv)

    assessment = load_chen(args.rubric)
    assessment.id = f"cli{int(time.time())}"
    store.save_assessment(assessment)
    config = runner.RunConfig(
        k=args.k, effort=args.effort, concurrency=args.concurrency,
        problem_ids=[p for p in args.problems.split(",") if p] or None,
        max_responses=args.max_responses,
    )
    run = runner.new_run(assessment, config)
    print(f"run {run.id}: {len(run.cells)} pairs x k={args.k} = {run.total_calls} calls "
          f"on {run.model} (effort {args.effort}, rubric {args.rubric}, prompt {run.prompt_version})")

    t0, last = time.time(), [0.0]

    def progress(r: runner.Run) -> None:
        if time.time() - last[0] > 10 or r.done_calls == r.total_calls:
            last[0] = time.time()
            print(f"  {r.done_calls}/{r.total_calls} calls, {r.cached_calls} cached, "
                  f"{r.failed_calls} failed, {time.time() - t0:.0f}s", flush=True)

    asyncio.run(runner.execute(run, assessment, progress))

    out_dir = store.data_dir() / "cli"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{args.rubric}_k{args.k}_{args.effort}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["instance_id", "pred", "confidence"])
        for c in run.cells.values():
            if c.final is not None:
                w.writerow([c.instance_id, c.final, c.confidence])

    u = run.usage
    print(f"\nstatus {run.status} in {time.time() - t0:.0f}s; tokens in {u.get('input_tokens', 0):,} "
          f"(cached {u.get('cached_tokens', 0):,}), out {u.get('output_tokens', 0):,} "
          f"(reasoning {u.get('reasoning_tokens', 0):,})")
    flagged = sum(1 for c in run.cells.values() if c.flags)
    print(f"cells {len(run.cells)}, flagged {flagged}, failed calls {run.failed_calls}")

    m = metrics.evaluate_run(run, assessment)
    if m:
        res, hum = m["run"], m["human"]
        o = res["overall"]
        print(f"\nvs human A / B: macro-F1 {_f(o['macro_f1_ref_a'])} / {_f(o['macro_f1_ref_b'])}, "
              f"item accuracy {_f(o['item_acc_ref_a'])} / {_f(o['item_acc_ref_b'])}")
        ceiling = {p["problem_id"]: p["human_exact_match"] for p in hum["problems"]}
        for p in res["problems"]:
            print(f"  {p['problem_id']}: exact-vector match {_f(p['exact_match_ref_a'])} / "
                  f"{_f(p['exact_match_ref_b'])}  (humans agree {_f(ceiling.get(p['problem_id']))}, "
                  f"n={p['n_responses']})")
        if "confidence" in res:
            c = res["confidence"]
            print(f"  confidence: AUROC {_f(c['auroc_flag_wrong'])}, {c['n_wrong']} wrong of "
                  f"{c['n_agreed_scored']} agreed; caught in least-confident 10/15/20%: "
                  f"{_f(c['wrong_caught_reviewing_10pct'])} / {_f(c['wrong_caught_reviewing_15pct'])} / "
                  f"{_f(c['wrong_caught_reviewing_20pct'])}")
    print(f"\npredictions: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
