from fastapi import APIRouter

import app.database
from app.database import get_dashboard_stats

router = APIRouter()


@router.get("/dashboard")
def dashboard():
    return get_dashboard_stats()
