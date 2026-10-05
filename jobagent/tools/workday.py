"""Workday career-site tools (public CXS endpoints, no key)."""
import httpx
from pydantic import BaseModel, Field

from jobagent.tools.common import (
    NORMALIZERS, FetchResult, NormalizedPosting, RawItem, content_hash, html_to_text,
    known_external_ids, store_raw_postings,
)
from jobagent.tools.http import describe_error, new_client, request_json
from jobagent.tools.registry import tool_spec

SOURCE = "workday"
PAGE_SIZE = 20  # Workday rejects or empties pages above 20
US = "United States of America"


class FetchWorkdayInput(BaseModel):
    site: str = Field(description=(
        "Workday site key 'tenant:wdN:site', e.g. 'nvidia:wd5:NVIDIAExternalCareerSite' "
        "(from https://<tenant>.<wdN>.myworkdayjobs.com/<site>)"))
    company: str | None = Field(None, description="Company display name, e.g. 'NVIDIA'; defaults to the tenant")
    search_text: str = Field("", description="Keyword search, e.g. 'treasury'; empty for newest postings")
    limit: int | None = Field(None, ge=1, description="Maximum new postings to store; omit for all")
    us_only: bool = Field(True, description="Store only postings located in the United States")
    refresh: bool = Field(False, description="Re-fetch postings that are already stored (to detect edits)")


def _external_id(listing: dict) -> str:
    bullets = listing.get("bulletFields") or []
    return bullets[0] if bullets else listing["externalPath"].rsplit("_", 1)[-1]


def normalize(payload: dict) -> NormalizedPosting:
    d = payload["detail"]
    locations = [d.get("location") or ""] + list(d.get("additionalLocations") or [])
    return NormalizedPosting(
        company=payload["company"],
        title=d["title"].strip(),
        location_text="; ".join(x for x in locations if x),
        description_text=html_to_text(d.get("jobDescription", "")),
        description_html=d.get("jobDescription") or "",
        employment_type=d.get("timeType"),
        requisition_id=d.get("jobReqId"),
        url=d.get("externalUrl"),
        posted_at=d.get("startDate"),
    )


NORMALIZERS[SOURCE] = normalize


@tool_spec(
    name="fetch_workday_jobs",
    description=(
        "Fetch postings from one company's Workday career site, newest first, and store them in the "
        "raw_postings table. Postings already stored are skipped. Returns counts and new row IDs."
    ),
)
def fetch_workday_jobs(args: FetchWorkdayInput) -> FetchResult:
    result = FetchResult(source=SOURCE, target=args.site)
    parts = args.site.split(":")
    if len(parts) != 3 or not all(parts):
        result.error = "site must look like 'tenant:wdN:site'"
        return result
    tenant, dc, site = parts
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    company = args.company or tenant.capitalize()
    known = set() if args.refresh else known_external_ids(SOURCE, args.site)
    items: list[RawItem] = []
    max_scan = None if args.limit is None else max(100, args.limit * 10)  # bound the work when filtering
    offset = scanned = 0
    try:
        with new_client() as client:
            while args.limit is None or len(items) < args.limit:
                page = request_json(client, "POST", f"{base}/jobs", json={
                    "appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": args.search_text,
                }).get("jobPostings") or []
                if not page:
                    break
                for listing in page:
                    scanned += 1
                    if not listing.get("externalPath"):  # some listings come back as stubs with no link
                        result.filtered_out += 1
                        continue
                    ext_id = _external_id(listing)
                    if ext_id in known:
                        result.already_present += 1
                        continue
                    detail = request_json(client, "GET", base + listing["externalPath"])["jobPostingInfo"]
                    if args.us_only and (detail.get("country") or {}).get("descriptor") != US:
                        result.filtered_out += 1
                        continue
                    payload = {"company": company, "listing": listing, "detail": detail}
                    items.append(RawItem(
                        external_id=ext_id, url=detail.get("externalUrl"), payload=payload,
                        content_hash=content_hash(html_to_text(detail.get("jobDescription", ""))),
                    ))
                    if args.limit is not None and len(items) >= args.limit:
                        break
                offset += PAGE_SIZE
                if max_scan is not None and scanned >= max_scan:
                    break
    except httpx.HTTPError as e:
        result.error = describe_error(e)  # keep what was fetched before the failure
    except (KeyError, TypeError, ValueError) as e:  # unexpected response shape
        result.error = f"unexpected response: {type(e).__name__}: {e}"
    stored = store_raw_postings(SOURCE, args.site, items)
    result.fetched = scanned
    result.inserted = stored.inserted
    result.already_present += stored.already_present
    result.raw_ids = stored.raw_ids
    return result
