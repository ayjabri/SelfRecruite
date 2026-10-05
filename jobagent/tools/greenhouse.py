"""Greenhouse job-board tools (public API, no key)."""
import html

import httpx
from pydantic import BaseModel, Field

from jobagent.tools.common import (
    NORMALIZERS, FetchResult, NormalizedPosting, RawItem, clean_requisition_id,
    content_hash, hours_since, html_to_text, known_external_ids,
    store_raw_postings,
)
from jobagent.tools.http import new_client, request_json
from jobagent.tools.registry import tool_spec

SOURCE = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


class FetchGreenhouseInput(BaseModel):
    board: str = Field(description="Greenhouse board slug, e.g. 'stripe' (from boards.greenhouse.io/<slug>)")
    limit: int | None = Field(None, ge=1, description="Maximum new postings to store; omit for all")
    posted_within_hours: int | None = Field(None, ge=1, description=(
        "Only postings from the last N hours (24 = last day, 48, 168 = last week); omit for any age"))


def _get_jobs(board: str) -> list[dict]:
    with new_client() as client:
        return request_json(
            client, "GET", API.format(board=board), params={"content": "true", "pay_transparency": "true"}
        )["jobs"]


def normalize(payload: dict) -> NormalizedPosting:
    n = NormalizedPosting(
        company=payload.get("company_name") or "Unknown",
        title=payload["title"].strip(),
        location_text=(payload.get("location") or {}).get("name", "").strip(),
        description_text=html_to_text(payload.get("content", "")),
        description_html=html.unescape(payload.get("content") or ""),
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
    # with a limit, spend it on postings not stored yet; without one, store everything
    known = known_external_ids(SOURCE, args.board) if args.limit else set()
    recent_enough = lambda j: args.posted_within_hours is None or (  # noqa: E731
        (hours_since(j.get("first_published")) or 0) <= args.posted_within_hours)
    too_old = sum(1 for j in jobs if not recent_enough(j))
    new = [j for j in jobs if str(j["id"]) not in known and recent_enough(j)][: args.limit]
    items = [
        RawItem(
            external_id=str(j["id"]), url=j.get("absolute_url"), payload=j,
            content_hash=content_hash(html_to_text(j.get("content", ""))),
        )
        for j in new
    ]
    result = store_raw_postings(SOURCE, args.board, items)
    result.fetched = len(jobs)
    result.filtered_out = too_old
    recent = [j for j in jobs if recent_enough(j)]
    result.already_present += len(recent) - len(new) if not args.limit else sum(str(j["id"]) in known for j in recent)
    return result
