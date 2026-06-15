import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles


# Run requirement-install BEFORE we import any app module that depends on
# them. The deployed server lives behind `git pull && systemctl restart`
# and we'd rather have one slow restart that auto-installs new deps than
# greet the user with an ImportError. Sample is fast (~10ms) when nothing
# is missing.

_REQ_FILE = Path(__file__).parent.parent / "requirements.txt"


def _ensure_requirements_installed():
    """Idempotent dependency check: read requirements.txt, try to import
    each top-level package, and if anything is missing run
    `pip install -r requirements.txt` once.

    Designed to be cheap on warm starts — when every package is
    already importable we never shell out. Skipped silently if the
    requirements file isn't present (e.g. installed via wheel)."""
    if not _REQ_FILE.exists():
        return

    # Map pip package name → import name where they differ. The vast
    # majority of our deps match (`fastapi`, `pandas`, `kaggle`, …),
    # so the table only carries the exceptions.
    PIP_TO_IMPORT = {
        "uvicorn[standard]": "uvicorn",
        "curl-cffi": "curl_cffi",
        "yfinance": "yfinance",
        "kaggle": "kaggle",
        "apscheduler": "apscheduler",
    }

    requirements = []
    for line in _REQ_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip version pins ('fastapi>=0.104.0' → 'fastapi')
        for sep in (">=", "<=", "==", ">", "<", "~="):
            if sep in line:
                line = line.split(sep, 1)[0].strip()
                break
        requirements.append(line)

    import importlib.util as _ilu
    missing = []
    for pkg in requirements:
        import_name = PIP_TO_IMPORT.get(pkg, pkg.replace("-", "_"))
        if _ilu.find_spec(import_name) is None:
            missing.append(pkg)

    if not missing:
        return

    print(
        f"[startup] {len(missing)} requirement(s) missing — running "
        f"pip install -r {_REQ_FILE.name}: {', '.join(missing)}",
        flush=True,
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(_REQ_FILE)],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # Don't crash the server on a pip failure — let the caller see
        # the original ImportError when it eventually tries to use the
        # missing package. Print the auto-install attempt so the
        # operator can fix it manually.
        print(
            f"[startup] pip install failed (exit {e.returncode}); "
            f"continuing — affected features will surface their own "
            f"ImportError when used.",
            flush=True,
        )


_ensure_requirements_installed()


from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.routes import dashboard, billionaires, analytics, scraper_api, export, families, insights, market


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Bloomberg Scraper", lifespan=lifespan)

# Compress JSON responses on the wire. Big payloads:
#   /api/billionaires/{id}/history    ~600KB → ~80KB
#   /api/analytics/concentration      ~600KB → ~50KB
#   /api/persons/{id}/profile         ~300KB → ~40KB
# minimum_size=500 skips the trivially-small responses where the
# CPU + Content-Encoding overhead isn't worth it.
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(dashboard.router, prefix="/api")
app.include_router(billionaires.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(scraper_api.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(families.router, prefix="/api")
app.include_router(insights.router, prefix="/api")
app.include_router(market.router, prefix="/api")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
