from abc import ABC, abstractmethod

import httpx

from app.config import Settings
from app.models.quota import ProviderSnapshot


class ProviderAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        ...
