from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.routes import dashboard, billionaires, analytics, scraper_api, export


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Bloomberg Scraper", lifespan=lifespan)

app.include_router(dashboard.router, prefix="/api")
app.include_router(billionaires.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(scraper_api.router, prefix="/api")
app.include_router(export.router, prefix="/api")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
