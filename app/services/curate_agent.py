"""One-shot diversity critic for curated briefs. Feedback loop runs at most once."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.angles import ANGLE_ORDER, normalize_angle
from app.services.llm_policy import parse_json_object

CRITIQUE_PURPOSE = "digest_critique"
MAX_FEEDBACK_LOOPS = 1


@dataclass
class Critique:
    ok: bool
    reason: str = ""
    missing: list[str] = field(default_factory=list)
    advice: str = ""


def build_critique_prompt(
    *,
    items: list[dict[str, str]],
    candidate_block: str,
    profile: str,
    topics: str,
) -> str:
    picked = []
    for i, row in enumerate(items, start=1):
        picked.append(
            f"{i}. angle={row.get('angle') or ''} kind={row.get('kind') or ''} "
            f"title={row.get('title') or ''} url={row.get('url') or ''} hint={row.get('hint') or ''}"
        )
    picked_text = "\n".join(picked)
    return (
        "You check Harumunjang diversity. Do the 3 picks give different insights?\n"
        "Too uniform? Same site or same event on repeat? "
        f"The 3 must be {ANGLE_ORDER[0]} (field trend), {ANGLE_ORDER[1]} (one hot issue), "
        f"{ANGLE_ORDER[2]} (notable person) — one each.\n"
        "Headline dump from one outlet → ok=false. Only one high-DAU site → ok=false.\n"
        "JSON only:\n"
        '{"ok":true,"reason":"...","missing":[],"advice":""}\n'
        "If ok=false, advice is a short English re-pick instruction (prefer other candidate URLs).\n"
        f"{profile}\n"
        f"Topics: {topics}\n"
        f"Picks:\n{picked_text}\n"
        f"Candidates:\n{candidate_block}\n"
    )


def parse_critique(text: str) -> Critique | None:
    data = parse_json_object(text or "")
    if not data or "ok" not in data:
        return None
    missing = data.get("missing") or []
    if not isinstance(missing, list):
        missing = []
    clean_missing = [str(x) for x in missing if normalize_angle(str(x)) or str(x)]
    advice = str(data.get("advice") or "").strip()
    reason = str(data.get("reason") or "").strip()
    ok = bool(data.get("ok"))
    return Critique(ok=ok, reason=reason[:240], missing=clean_missing[:3], advice=advice[:400])


def should_retry(critique: Critique | None, loops_used: int) -> bool:
    if loops_used >= MAX_FEEDBACK_LOOPS:
        return False
    return critique is not None and not critique.ok
