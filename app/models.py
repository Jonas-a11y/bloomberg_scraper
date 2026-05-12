from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_wealth: int
    count: int
    snapshots: int
    latest_scrape: str | None


class BillionaireRow(BaseModel):
    person_id: int
    rank: int
    common_name: str | None
    full_name: str | None
    citizenship: str | None
    age: int | None
    birth_year: int | None
    gender: str | None
    gender_confidence: float | None
    industry: str | None
    sector: str | None
    net_worth_usd: int | None
    last_change_usd: int | None
    last_change_pct: float | None
    ytd_change_usd: int | None
    ytd_change_pct: float | None


class BillionaireList(BaseModel):
    data: list[BillionaireRow]
    total: int
    page: int
    pages: int


class ScraperStatus(BaseModel):
    status: str
    next_run: str | None
    last_success: str | None


class ScrapeRun(BaseModel):
    id: int
    started_at: str
    finished_at: str | None
    status: str
    record_count: int | None
    duration_ms: int | None
    error: str | None


class ScheduleConfig(BaseModel):
    times: list[str]
    timezone: str
    enabled: bool


class ScheduleUpdate(BaseModel):
    times: list[str]
    timezone: str
    enabled: bool
