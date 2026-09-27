"""Agent 8 -- Report Generator.

Compiles everything the pipeline has produced into the final structured
review record (overall status, confidence, summary) that gets persisted
by services/analysis_service.py and rendered by the frontend.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext

SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}


class ReportAgent(Agent):
    name = "report_generator"

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        issues = ctx.issues
        if not issues:
            ctx.review_status = "APPROVED"
            ctx.review_summary = (
                f"No issues found across {len(ctx.changed_symbols)} changed symbol(s) "
                f"in {len(ctx.commit.files)} file(s)."
            )
            ctx.review_confidence = 0.75
            ctx.log(self.name, "Report: APPROVED, no issues found.")
            return ctx

        worst = max(issues, key=lambda i: SEVERITY_ORDER.get(str(i.get("severity", "")).upper(), 0))
        worst_severity = str(worst.get("severity", "MEDIUM")).upper()

        if worst_severity in ("CRITICAL", "HIGH"):
            ctx.review_status = "NEEDS_CHANGES"
        elif worst_severity == "MEDIUM":
            ctx.review_status = "NEEDS_CHANGES"
        else:
            ctx.review_status = "APPROVED_WITH_SUGGESTIONS"

        confidences = [float(i.get("confidence", 0.5) or 0.5) for i in issues]
        ctx.review_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        by_severity = {}
        for i in issues:
            sev = str(i.get("severity", "MEDIUM")).upper()
            by_severity[sev] = by_severity.get(sev, 0) + 1
        severity_summary = ", ".join(f"{v} {k}" for k, v in sorted(by_severity.items(), key=lambda kv: -SEVERITY_ORDER.get(kv[0], 0)))

        patched = sum(1 for i in issues if i.get("suggested_patch"))
        verified = sum(1 for i in issues if i.get("verification_status") == "VERIFIED")

        ctx.review_summary = (
            f"Found {len(issues)} issue(s) across {len(ctx.change_groups)} independent change "
            f"group(s): {severity_summary}. {patched} patch(es) generated, {verified} verified "
            f"by running tests."
        )
        ctx.log(self.name, f"Report: {ctx.review_status} ({ctx.review_summary})")
        return ctx
