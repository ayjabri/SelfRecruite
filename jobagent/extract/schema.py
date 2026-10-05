from typing import Literal

from pydantic import BaseModel


class Location(BaseModel):
    city: str | None
    state: str | None
    country: str | None


class JobExtract(BaseModel):
    company: str
    job_title: str
    requisition_id: str | None
    locations: list[Location]            # city, state, country
    workplace_type: Literal["onsite", "hybrid", "remote", "unknown"]
    employment_type: Literal["full_time", "contract", "part_time", "unknown"]
    seniority: Literal["entry", "mid", "senior", "lead", "director", "vp_plus", "unknown"]
    summary: str                          # 2-3 sentences, own words not the JD's
    responsibilities: list[str]
    required_qualifications: list[str]
    preferred_qualifications: list[str]
    skills: list[str]
    min_years_experience: int | None
    education: str | None
    comp_min: float | None
    comp_max: float | None
    comp_period: Literal["year", "hour", "unknown"]
    comp_text: str | None                 # the exact sentence the numbers came from
    visa_sponsorship: Literal["yes", "no", "unstated"]
    clearance_required: bool
