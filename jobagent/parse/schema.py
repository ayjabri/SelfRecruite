from typing import Literal

from pydantic import BaseModel

from jobagent.extract.schema import Location

SectionLabel = Literal["responsibilities", "required", "preferred", "other"]


class Section(BaseModel):
    heading: str
    label: SectionLabel
    bullets: list[str]


class ParsedJob(BaseModel):
    """Everything code can read from a posting without an LLM."""
    locations: list[Location] = []
    workplace_type: Literal["onsite", "hybrid", "remote", "unknown"] = "unknown"
    employment_type: Literal["full_time", "contract", "part_time", "unknown"] = "unknown"
    seniority: str = "unknown"
    min_years_experience: int | None = None
    education: list[str] = []            # degree levels mentioned: bachelor, master, mba, phd
    comp_min: float | None = None
    comp_max: float | None = None
    comp_period: Literal["year", "hour", "unknown"] = "unknown"
    comp_text: str | None = None         # the sentence the numbers came from
    visa_sponsorship: Literal["yes", "no", "unstated"] = "unstated"
    clearance_required: bool = False
    sections: list[Section] = []
    # labelled: headings told required/preferred apart; unlabelled: bullets found but no usable
    # headings (all bullets count as requirements); none: no bullets at all
    sections_quality: Literal["labelled", "unlabelled", "none"] = "none"

    def bullets(self, label: str) -> list[str]:
        return [b for s in self.sections if s.label == label for b in s.bullets]

    @property
    def requirements(self) -> list[str]:
        """What scoring treats as required: labelled sections, or every bullet if none are labelled."""
        if self.sections_quality == "unlabelled":
            return [b for s in self.sections for b in s.bullets]
        return self.bullets("required")
