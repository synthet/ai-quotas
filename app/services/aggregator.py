import asyncio
import traceback
from datetime import datetime

import httpx

from app.config import Settings
from app.http import create_client
from app.models.quota import DashboardSnapshot, ProviderSnapshot, ProviderStatus
from app.providers import adapters_for_settings, sort_provider_snapshots


async def refresh_snapshot(settings: Settings) -> DashboardSnapshot:
    fetched_at = datetime.utcnow()
    adapters = adapters_for_settings(settings)
    async with create_client() as client:
        tasks = [adapter.fetch(client, settings) for adapter in adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    providers: list[ProviderSnapshot] = []
    for adapter, result in zip(adapters, results):
        if isinstance(result, Exception):
            providers.append(
                ProviderSnapshot(
                    provider=adapter.provider_id,
                    status=ProviderStatus.ERROR,
                    message=f"{type(result).__name__}: {result}",
                    fetched_at=fetched_at,
                )
            )
            traceback.print_exception(type(result), result, result.__traceback__)
        else:
            providers.append(result)

    providers = sort_provider_snapshots(providers)
    return DashboardSnapshot(fetched_at=fetched_at, providers=providers)
