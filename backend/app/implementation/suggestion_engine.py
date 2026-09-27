"""
suggestion_engine -- normalizes and quality-filters the `suggestions`
list from the ImplementationAgent's structured response into precise,
actionable Suggestion records (spec section 9: file + symbol + exact
location + proposed change + reason + expected behavior, never a vague
one-liner like "improve security").

Vague suggestions are dropped rather than passed through: a suggestion
that has no file, no concrete proposed_change, or no reason is not
actionable and would mislead a user into thinking real analysis
happened. This is a filter on the agent's own output, not a second LLM
call -- it keeps the system honest about what the model actually
grounded in real code versus what it hand-waved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_MIN_WORDS_FOR_SUBSTANTIVE_TEXT = 3
_VAGUE_PHRASES = {
    "improve security", "improve performance", "improve code quality",
    "make it better", "clean up the code", "add error handling",
    "improve error handling", "refactor this", "improve this",
}


@dataclass
class Suggestion:
    file: str
    symbol: str
    location: str
    change_type: str  # required | recommended | optional
    current_behavior: str
    proposed_change: str
    reason: str
    expected_behavior: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "location": self.location,
            "change_type": self.change_type,
            "current_behavior": self.current_behavior,
            "proposed_change": self.proposed_change,
            "reason": self.reason,
            "expected_behavior": self.expected_behavior,
        }


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _is_vague(proposed_change: str) -> bool:
    normalized = (proposed_change or "").strip().lower().rstrip(".")
    if normalized in _VAGUE_PHRASES:
        return True
    return _word_count(proposed_change) < _MIN_WORDS_FOR_SUBSTANTIVE_TEXT


def build_suggestions(raw_suggestions: list) -> list:
    """Returns list[Suggestion], silently dropping entries that don't
    meet the "precise, not vague" bar. Callers that want to know what
    was dropped and why can pass `raw_suggestions` through
    `filter_report` instead."""
    result = []
    for raw in raw_suggestions or []:
        if not isinstance(raw, dict):
            continue
        file_path = str(raw.get("file") or "").strip()
        proposed_change = str(raw.get("proposed_change") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        expected = str(raw.get("expected_behavior") or "").strip()

        if not file_path or not proposed_change or not reason:
            continue
        if _is_vague(proposed_change) or _is_vague(reason):
            continue

        change_type = str(raw.get("change_type") or "recommended").strip().lower()
        if change_type not in ("required", "recommended", "optional"):
            change_type = "recommended"

        result.append(
            Suggestion(
                file=file_path,
                symbol=str(raw.get("symbol") or "") or "",
                location=str(raw.get("location") or "").strip(),
                change_type=change_type,
                current_behavior=str(raw.get("current_behavior") or "").strip(),
                proposed_change=proposed_change,
                reason=reason,
                expected_behavior=expected,
            )
        )
    return result


def filter_report(raw_suggestions: list) -> dict:
    """Like build_suggestions, but also reports what was dropped and why
    -- useful for the API/UI to be transparent about quality filtering
    rather than silently returning fewer suggestions than the model gave."""
    kept = []
    dropped = []
    for raw in raw_suggestions or []:
        if not isinstance(raw, dict):
            dropped.append({"raw": raw, "reason": "not an object"})
            continue
        before = len(kept)
        kept_batch = build_suggestions([raw])
        if kept_batch:
            kept.extend(kept_batch)
        else:
            dropped.append({"raw": raw, "reason": "missing file/proposed_change/reason, or too vague to act on"})
    return {"suggestions": [s.to_dict() for s in kept], "dropped_count": len(dropped), "dropped": dropped}
