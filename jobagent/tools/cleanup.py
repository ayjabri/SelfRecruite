"""Cleanup tools: remove jobs that no longer meet your criteria.

Deleting a job also removes its links and any possible-duplicate rows (database cascades).
The raw postings behind it are kept and marked filtered, so the next pull neither re-fetches
nor re-parses them.
"""
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update

from jobagent.db import session
from jobagent.models import Job, JobSource, RawPosting, UserJob
from jobagent.tools.filtering import FILTERED, load_filters
from jobagent.tools.geo import country_code
from jobagent.tools.registry import tool_spec

_REMOTE = re.compile(r"\bremote\b", re.I)


class PurgeInput(BaseModel):
    outside_states: list[str] | None = Field(None, description=(
        "Delete jobs located only outside these US states (codes, e.g. ['NY', 'NJ']). "
        "Default: location.allowed_states from config/filters.yaml."))
    outside_countries: list[str] | None = Field(None, description=(
        "Also delete jobs located only outside these countries (ISO codes). Default: the platform's "
        "countries from config/filters.yaml. [] skips the country check."))
    keep_remote: bool = Field(True, description="Keep remote jobs wherever they are located")
    keep_unknown: bool = Field(True, description="Keep jobs whose state could not be read")
    keep_marked: bool = Field(True, description=(
        "Keep jobs any user marked interested, favorite, applied, or wrote notes on"))
    dry_run: bool = Field(True, description="Only report what would be deleted. Set false to delete.")


class PurgeResult(BaseModel):
    matched: int = 0
    deleted: int = 0
    kept_marked: int = 0
    postings_marked_filtered: int = 0
    sample: list[str] = []
    dry_run: bool = True
    error: str | None = None


def _states(job: Job) -> set[str]:
    return {loc.get("state") for loc in job.locations or [] if loc.get("state")}


@tool_spec(
    name="purge_jobs",
    description=(
        "Delete jobs outside the platform's countries, or not in the allowed states and not remote. Marked jobs "
        "are kept by default. Dry run by default: pass dry_run=false to delete. The underlying "
        "postings stay stored as filtered so they are not pulled again."
    ),
)
def purge_jobs(args: PurgeInput) -> PurgeResult:
    allowed = {s.upper() for s in (args.outside_states if args.outside_states is not None
                                   else load_filters()["allowed_states"])}
    if not allowed:
        return PurgeResult(dry_run=args.dry_run, error="no states given and none configured")
    countries = {c.upper() for c in (args.outside_countries if args.outside_countries is not None
                                     else load_filters()["countries"])}
    out = PurgeResult(dry_run=args.dry_run)
    with session() as s, s.begin():
        jobs = s.scalars(select(Job).where(Job.duplicate_of.is_(None)).order_by(Job.id)).all()
        marked_ids = set(s.scalars(select(UserJob.job_id).where(
            (UserJob.user_label == "interested") | UserJob.favorite | UserJob.applied_at.is_not(None) | UserJob.notes.is_not(None))))
        doomed: list[int] = []
        for j in jobs:
            states = _states(j)
            known = {country_code(loc.get("country")) or loc.get("country") for loc in j.locations or [] if loc.get("country")}
            foreign = bool(countries and known and not (known & countries))
            if args.keep_remote and not foreign and (
                    j.workplace_type == "remote" or any(_REMOTE.search(loc.get("city") or "") for loc in j.locations or [])):
                continue
            if not foreign:
                if not states:
                    if args.keep_unknown:
                        continue
                elif states & allowed:
                    continue
            out.matched += 1
            if args.keep_marked and j.id in marked_ids:
                out.kept_marked += 1
                continue
            doomed.append(j.id)
            if len(out.sample) < 10:
                out.sample.append(f"{j.title} [{', '.join(sorted(known - {'US'}) or sorted(states)) or 'no state'}]")
        if not doomed:
            return out
        # merged copies go with their canonical job
        doomed += list(s.scalars(select(Job.id).where(Job.duplicate_of.in_(doomed))))
        raw_ids = list(s.scalars(select(JobSource.raw_id).where(JobSource.job_id.in_(doomed))))
        out.deleted = len(doomed)
        out.postings_marked_filtered = len(raw_ids)
        if args.dry_run:
            s.rollback()
            return out
        reason = f"purged {datetime.now(timezone.utc).date()}: outside {'/'.join(sorted(allowed))}" + (f" / {'/'.join(sorted(countries))}" if countries else "")
        if raw_ids:
            s.execute(update(RawPosting).where(RawPosting.id.in_(raw_ids))
                      .values(filter_status=FILTERED, filter_reason=reason))
        s.execute(delete(Job).where(or_(Job.id.in_(doomed), Job.duplicate_of.in_(doomed))))
    return out
