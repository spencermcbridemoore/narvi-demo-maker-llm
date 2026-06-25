# Convenience targets (macOS / Linux). On Windows, use `python run.py` directly.
.PHONY: dev install backend frontend clean

# Start backend + frontend together.
dev:
	python run.py

# One-time setup: venv + Python deps + frontend deps.
install:
	python -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	npm --prefix frontend install

# Run just the backend (with autoreload) or frontend.
backend:
	.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

frontend:
	npm --prefix frontend run dev

# Remove the runtime SQLite checkpoint(s).
clean:
	rm -f backend/app/data/checkpoints.sqlite*
