import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings
from app.models.quota import DashboardSnapshot
from app.services.aggregator import refresh_snapshot
from app.state import AppState

ROOT = Path(__file__).resolve().parent.parent
jinja_env = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)


def _fmt_metric(val):
    if val is None:
        return "—"
    if isinstance(val, float):
        if val == int(val) and abs(val) < 1e6:
            return str(int(val))
        return f"{val:.1f}" if abs(val) >= 10 else f"{val:.2f}"
    return val


jinja_env.filters["fmt"] = _fmt_metric
app_state = AppState()

APP_VERSION = "0.1.0"


async def _run_refresh() -> None:
    if app_state.refresh_lock.locked():
        return
    async with app_state.refresh_lock:
        app_state.refreshing = True
        try:
            settings = get_settings()
            app_state.snapshot = await refresh_snapshot(settings)
            app_state.last_refresh = datetime.utcnow()
        finally:
            app_state.refreshing = False


async def _poll_loop() -> None:
    while True:
        settings = get_settings()
        interval = max(30, settings.probe_interval_sec)
        await _run_refresh()
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AI Quotas Dashboard", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _snapshot_or_empty() -> DashboardSnapshot:
    if app_state.snapshot:
        return app_state.snapshot
    return DashboardSnapshot(fetched_at=datetime.utcnow(), providers=[])


def _template_context(request: Request) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "snapshot": _snapshot_or_empty(),
        "last_refresh": app_state.last_refresh,
        "refreshing": app_state.refreshing,
        "probe_interval_sec": settings.probe_interval_sec,
        "quota_scope": settings.quota_scope,
    }


def _render(template_name: str, request: Request) -> HTMLResponse:
    html = jinja_env.get_template(template_name).render(_template_context(request))
    return HTMLResponse(html)


@app.get("/")
async def dashboard(request: Request):
    return _render("dashboard.html", request)


@app.get("/fragments/providers")
async def providers_fragment(request: Request):
    return _render("partials/providers.html", request)


@app.get("/api/health")
async def health():
    settings = get_settings()
    return {
        "version": APP_VERSION,
        "quota_scope": settings.quota_scope,
        "last_refresh": app_state.last_refresh.isoformat() + "Z" if app_state.last_refresh else None,
        "refreshing": app_state.refreshing,
    }


@app.get("/api/snapshot")
async def api_snapshot():
    snap = _snapshot_or_empty()
    return JSONResponse(content=snap.model_dump(mode="json"))


@app.post("/api/refresh")
async def api_refresh():
    await _run_refresh()
    snap = _snapshot_or_empty()
    return JSONResponse(content=snap.model_dump(mode="json"))
