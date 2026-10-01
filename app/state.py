import asyncio
from datetime import datetime

from app.models.quota import DashboardSnapshot


class AppState:
    def __init__(self) -> None:
        self.snapshot: DashboardSnapshot | None = None
        self.last_refresh: datetime | None = None
        self.refresh_lock = asyncio.Lock()
        self.refreshing = False
