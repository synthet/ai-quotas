import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
