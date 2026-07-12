from pathlib import Path

from fastapi.staticfiles import StaticFiles

from planning_api import app

WEB_DIR = Path(__file__).resolve().parent / "web"

if not WEB_DIR.exists():
    raise RuntimeError(f"Web directory not found: {WEB_DIR}")

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
