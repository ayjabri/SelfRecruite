"""LLM judgement of one job against the golden-source resume (BUILD_PLAN step 5)."""
import re
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from sqlalchemy import select

from jobagent.db import session
from jobagent.llm import get_chat_model
from jobagent.models import GoldenChunk

CHUNKS_PER_REQUIREMENT = 3
MAX_CONTEXT_CHARS = 22_000   # keeps the prompt inside the model's context window
Verdict = Literal["Covered", "Augmentable", "Reframe", "Gap"]

ANCHORS = (
    "Percentage anchors (score meaning over wording):\n"
    "  90-100: the same work at the same scope.\n"
    "  70-89:  the same work at narrower scope, or in a different domain.\n"
    "  40-69:  related work, but a real part of the requirement is missing.\n"
    "  10-39:  adjacent experience only.\n"
    "  0-9:    nothing relevant."
)
SYSTEM = (
    "You judge how well a candidate's resume covers each requirement of a job. The RESUME EXCERPTS "
    "below are the only facts about the candidate; never assume anything that is not in them.\n"
    "For every requirement give:\n"
    "- cls: Covered (the excerpts show it), Augmentable (shown, but a detail or keyword could be added "
    "from the excerpts), Reframe (shown under different wording or domain), or Gap (not shown).\n"
    "- pct: 0-100 using the anchors.\n"
    "- evidence_section: the heading of the excerpt that proves it, copied exactly, or null for a Gap.\n"
    "- evidence_quote: a short phrase copied word for word from that excerpt, or null for a Gap.\n"
    "- missing: one short line on what the excerpts do not show, or null.\n"
    + ANCHORS
)


class RequirementVerdict(BaseModel):
    index: int = Field(description="The requirement's number in the list")
    cls: Verdict
    pct: int = Field(ge=0, le=100)
    evidence_section: str | None = None
    evidence_quote: str | None = None
    missing: str | None = None


class Judgement(BaseModel):
    verdicts: list[RequirementVerdict]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _quote_in(quote: str | None, texts) -> bool:
    """A quote counts when it appears in an excerpt, allowing small differences (punctuation, a word)."""
    q = _norm(quote or "")
    if len(q) < 8:
        return False
    return any(q in _norm(t) or fuzz.partial_ratio(q, _norm(t)) >= 90 for t in texts)


def retrieve(requirements: list[str]) -> tuple[dict[str, str], list[list[str]]]:
    """Top chunks per requirement. Returns ({heading: text}, [headings per requirement])."""
    from jobagent.tools.embeddings import QUERY, embed_texts  # here, not at the top: avoids an import cycle
    vectors = embed_texts(requirements, QUERY)
    chunks: dict[str, str] = {}
    per_req: list[list[str]] = []
    with session() as s:
        for v in vectors:
            rows = s.execute(
                select(GoldenChunk.heading, GoldenChunk.text).where(~GoldenChunk.always_include)
                .order_by(GoldenChunk.embedding.cosine_distance(v)).limit(CHUNKS_PER_REQUIREMENT)
            ).all()
            per_req.append([h for h, _ in rows])
            for h, t in rows:
                chunks.setdefault(h, t)
    return chunks, per_req


def judge(title: str, company: str, requirements: list[tuple[str, str]]) -> list[dict]:
    """requirements: [(label, text)]. Returns one dict per requirement, in order, with verified evidence."""
    texts = [t for _, t in requirements]
    chunks, per_req = retrieve(texts)
    budget, kept = MAX_CONTEXT_CHARS, {}
    for heading in (h for hs in per_req for h in hs):  # keep the most relevant first, within budget
        if heading not in kept and len(chunks[heading]) <= budget:
            kept[heading] = chunks[heading]
            budget -= len(chunks[heading])
    excerpts = "\n\n".join(f"### {h}\n{t}" for h, t in kept.items())
    listing = "\n".join(f"{i + 1}. [{label}] {t}" for i, (label, t) in enumerate(requirements))
    model = get_chat_model().with_structured_output(Judgement)
    result: Judgement = model.invoke([
        ("system", SYSTEM),
        ("user", f"JOB: {title} at {company}\n\nREQUIREMENTS:\n{listing}\n\nRESUME EXCERPTS:\n{excerpts}\n\n"
                 f"Return one verdict per requirement, {len(requirements)} in total, in order."),
    ])
    by_index = {v.index: v for v in result.verdicts}
    out = []
    for i, (label, text) in enumerate(requirements):
        v = by_index.get(i + 1)
        if v is None:
            out.append({"label": label, "text": text, "cls": "Gap", "pct": 0, "evidence_section": None,
                        "evidence_quote": None, "missing": "no verdict returned", "evidence_verified": False})
            continue
        quote_ok = _quote_in(v.evidence_quote, kept.values())
        section_ok = v.evidence_section in kept
        pct = v.pct
        if v.cls != "Gap" and not quote_ok:
            pct = min(pct, 60)  # claims without a verifiable quote cannot count as covered
        out.append({"label": label, "text": text, "cls": v.cls, "pct": pct,
                    "evidence_section": v.evidence_section if section_ok else None,
                    "evidence_quote": v.evidence_quote if quote_ok else None,
                    "missing": v.missing, "evidence_verified": quote_ok})
    return out
