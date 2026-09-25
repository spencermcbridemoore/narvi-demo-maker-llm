"""Rubric grader: grade free-text responses against binary rubric items.

Lives inside DemoBuilder for now, sharing its Azure access, config and deploy, but
is self-contained: the only DemoBuilder imports are ``app.config`` and ``app.llm``.
Moving it to its own repo later is a copy of this package plus ``app/llm``.

    model.py      internal data model (every input format is adapted into it)
    adapters.py   Chen & Wan kit + generic CSV  ->  Assessment
    grade.py      one LLM call: (problem, criterion, response) -> Sample
    aggregate.py  k samples -> binary final, confidence, review flags
    runner.py     batch runs: concurrency cap, cache, progress, cancel
    metrics.py    scores a run with the kit's tools/evaluate.py when reference labels exist
    store.py      in-memory + JSON-on-disk persistence, SQLite sample cache
    api.py        FastAPI router mounted at /grader/api
    cli.py        the same runner from the command line (benchmarking)
"""
