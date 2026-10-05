"""Greenhouse job-board tools (public API, no key)."""
import httpx
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobagent.tools.common import (
    NORMALIZERS, FetchResult, NormalizedPosting, RawItem, clean_requisition_id,
    content_hash, html_to_text, store_raw_postings,
)
from jobagent.tools.registry import tool_spec

SOURCE = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


class FetchGreenhouseInput(BaseModel):
    board: str = Field(description="Greenhouse board slug, e.g. 'stripe' (from boards.greenhouse.io/<slug>)")
    limit: int | None = Field(None, ge=1, description="Maximum postings to store; omit for all")


@retry(
    stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True,
    retry=retry_if_exception_type(httpx.TransportError),
)
def _get_jobs(board: str) -> list[dict]:
    with httpx.Client(timeout=30) as client:
        r = client.get(API.format(board=board), params={"content": "true", "pay_transparency": "true"})
        r.raise_for_status()
        return r.json()["jobs"]


def normalize(payload: dict) -> NormalizedPosting:
    n = NormalizedPosting(
        company=payload.get("company_name") or "Unknown",
        title=payload["title"].strip(),
        location_text=(payload.get("location") or {}).get("name", "").strip(),
        description_text=html_to_text(payload.get("content", "")),
        requisition_id=clean_requisition_id(payload.get("requisition_id")),
        url=payload.get("absolute_url"),
        posted_at=payload.get("first_published"),
    )
    ranges = [r for r in payload.get("pay_input_ranges") or [] if r.get("min_cents") and r.get("max_cents")]
    if ranges:
        n.comp_min = min(r["min_cents"] for r in ranges) / 100
        n.comp_max = max(r["max_cents"] for r in ranges) / 100
        n.comp_period = "hour" if n.comp_max < 1000 else "year"
    return n


NORMALIZERS[SOURCE] = normalize


@tool_spec(
    name="fetch_greenhouse_jobs",
    description=(
        "Fetch the open postings on one Greenhouse job board and store them in the raw_postings "
        "table. Safe to rerun: postings already stored are skipped. Returns counts and new row IDs."
    ),
)
def fetch_greenhouse_jobs(args: FetchGreenhouseInput) -> FetchResult:
    try:
        jobs = _get_jobs(args.board)
    except httpx.HTTPStatusError as e:
        hint = " (board slug not found)" if e.response.status_code == 404 else ""
        return FetchResult(source=SOURCE, target=args.board, error=f"HTTP {e.response.status_code}{hint}")
    except httpx.HTTPError as e:
        return FetchResult(source=SOURCE, target=args.board, error=f"{type(e).__name__}: {e}")
    items = [
        RawItem(
            external_id=str(j["id"]), url=j.get("absolute_url"), payload=j,
            content_hash=content_hash(html_to_text(j.get("content", ""))),
        )
        for j in jobs[: args.limit]
    ]
    return store_raw_postings(SOURCE, args.board, items)
