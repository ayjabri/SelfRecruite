"""Oracle Recruiting Cloud tools (public Candidate Experience REST API, no key)."""
import httpx
from pydantic import BaseModel, Field

from jobagent.tools.common import (
    NORMALIZERS, FetchResult, NormalizedPosting, RawItem, content_hash, days_since, hours_to_days, html_to_text,
    known_external_ids, store_raw_postings,
)
from jobagent.tools.http import describe_error, new_client, request_json
from jobagent.tools.registry import tool_spec

SOURCE = "oracle"
PAGE_SIZE = 25
_DESCRIPTION_PARTS = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")


class FetchOracleInput(BaseModel):
    host: str = Field(description="Oracle Cloud host from the careers URL, e.g. 'jpmc.fa.oraclecloud.com'")
    site_number: str = Field(description="Career site number from the careers URL, e.g. 'CX_1001'")
    company: str = Field(description="Company display name, e.g. 'JPMorgan Chase'")
    keyword: str = Field("", description="Keyword search, e.g. 'treasury'; empty for newest postings")
    limit: int | None = Field(None, ge=1, description="Maximum new postings to store; omit for all")
    us_only: bool = Field(True, description="Store only postings whose primary location is in the United States")
    posted_within_hours: int | None = Field(None, ge=1, description=(
        "Only postings from the last N hours (24 = last day, 48, 168 = last week); omit for any age"))
    refresh: bool = Field(False, description="Re-fetch postings that are already stored (to detect edits)")


def _description_html(detail: dict) -> str:
    return "\n".join(detail.get(k) or "" for k in _DESCRIPTION_PARTS)


def normalize(payload: dict) -> NormalizedPosting:
    d = payload["detail"]
    secondary = [x.get("Name", "") for x in d.get("secondaryLocations") or []]
    return NormalizedPosting(
        company=payload["company"],
        title=d["Title"].strip(),
        location_text="; ".join(x for x in [d.get("PrimaryLocation") or "", *secondary] if x),
        description_text=html_to_text(_description_html(d)),
        description_html=_description_html(d),
        employment_type=d.get("JobSchedule"),
        requisition_id=str(d["Id"]),
        url=payload.get("url"),
        posted_at=d.get("ExternalPostedStartDate"),
    )


NORMALIZERS[SOURCE] = normalize


@tool_spec(
    name="fetch_oracle_jobs",
    description=(
        "Fetch postings from one company's Oracle Recruiting Cloud career site, newest first, and store "
        "them in the raw_postings table. Postings already stored are skipped. Returns counts and new row IDs."
    ),
)
def fetch_oracle_jobs(args: FetchOracleInput) -> FetchResult:
    target = f"{args.host}/{args.site_number}"
    result = FetchResult(source=SOURCE, target=target)
    api = f"https://{args.host}/hcmRestApi/resources/latest"
    known = set() if args.refresh else known_external_ids(SOURCE, target)
    max_days = hours_to_days(args.posted_within_hours)
    keyword = f',keyword="{args.keyword}"' if args.keyword else ""
    items: list[RawItem] = []
    max_scan = None if args.limit is None else max(100, args.limit * 10)  # bound the work when filtering
    offset = scanned = 0
    try:
        with new_client() as client:
            while args.limit is None or len(items) < args.limit:
                finder = (f"findReqs;siteNumber={args.site_number},limit={PAGE_SIZE},offset={offset}"
                          f"{keyword},sortBy=POSTING_DATES_DESC")
                data = request_json(client, "GET", f"{api}/recruitingCEJobRequisitions", params={
                    "onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder,
                })
                page = (data.get("items") or [{}])[0].get("requisitionList") or []
                if not page:
                    break
                stop = False
                for listing in page:
                    scanned += 1
                    age = days_since(listing.get("PostedDate"))  # Oracle lists a date, not a time
                    if max_days is not None and age is not None and age > max_days:
                        stop = True  # sorted by posting date, newest first
                        break
                    ext_id = str(listing["Id"])
                    if ext_id in known:
                        result.already_present += 1
                        continue
                    if args.us_only and listing.get("PrimaryLocationCountry") != "US":
                        result.filtered_out += 1
                        continue
                    found = request_json(client, "GET", f"{api}/recruitingCEJobRequisitionDetails", params={
                        "onlyData": "true", "expand": "all",
                        "finder": f'ById;Id="{ext_id}",siteNumber={args.site_number}',
                    }).get("items") or []
                    if not found:
                        result.filtered_out += 1
                        continue
                    url = f"https://{args.host}/hcmUI/CandidateExperience/en/sites/{args.site_number}/job/{ext_id}"
                    payload = {"company": args.company, "url": url, "listing": listing, "detail": found[0]}
                    items.append(RawItem(
                        external_id=ext_id, url=url, payload=payload,
                        content_hash=content_hash(html_to_text(_description_html(found[0]))),
                    ))
                    if args.limit is not None and len(items) >= args.limit:
                        break
                offset += PAGE_SIZE
                if stop or (max_scan is not None and scanned >= max_scan):
                    break
    except httpx.HTTPError as e:
        result.error = describe_error(e)  # keep what was fetched before the failure
    except (KeyError, TypeError, ValueError) as e:  # unexpected response shape
        result.error = f"unexpected response: {type(e).__name__}: {e}"
    stored = store_raw_postings(SOURCE, target, items)
    result.fetched = scanned
    result.inserted = stored.inserted
    result.already_present += stored.already_present
    result.raw_ids = stored.raw_ids
    return result
