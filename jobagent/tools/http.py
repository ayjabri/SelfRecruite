"""One retrying JSON request helper for every source tool."""
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (jobagent)"}


def _retryable(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True,
       retry=retry_if_exception(_retryable))
def request_json(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    r = client.request(method, url, **kwargs)
    r.raise_for_status()
    return r.json()


def new_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True)


def describe_error(e: httpx.HTTPError) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from {e.request.url.host}"
    return f"{type(e).__name__}: {e}"
