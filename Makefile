dev:
	backend_pid=; .venv/bin/uvicorn backend.app.main:app --reload --port 8000 & backend_pid=$$!; trap 'kill $$backend_pid 2>/dev/null || true' EXIT INT TERM; cd frontend && npm run dev -- --host 0.0.0.0

	PYTHONPATH=. .venv/bin/pytest -q

frontend-install:
	cd frontend && npm install

frontend-build:
	cd frontend && npm run build
