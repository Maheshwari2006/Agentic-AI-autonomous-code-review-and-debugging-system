"""Agent 2 -- Change Analyzer.

Turns the raw per-file diff into symbol-level changes (added/modified/
removed functions, methods, classes) and clusters those into independent
logical change groups, per the "a commit may contain multiple unrelated
changes" requirement.
"""
from __future__ import annotations

from .base import Agent, AnalysisContext
from ..analysis.change_detection import (
    detect_changed_symbols,
    detect_changed_symbols_no_parser,
    group_changes,
)
from ..logging_config import get_logger

logger = get_logger(__name__)


class ChangeAnalyzerAgent(Agent):
    name = "change_analyzer"

    def run(self, ctx: AnalysisContext) -> AnalysisContext:
        all_changes = []
        for f in ctx.commit.files:
            if f.status == "removed":
                changes = detect_changed_symbols(f.filename, f.previous_content, None)
            else:
                changes = detect_changed_symbols(f.filename, f.previous_content, f.current_content)

            if not changes and f.patch:
                # language unsupported or content unavailable -- don't
                # silently drop the file, surface it as a whole-file unit
                changes = detect_changed_symbols_no_parser(f.filename, f.patch)
                ctx.log(
                    self.name,
                    f"{f.filename}: no symbol-level parser available, tracked as whole-file change",
                )

            all_changes.extend(changes)

        ctx.changed_symbols = all_changes
        ctx.change_groups = group_changes(all_changes)

        summary = ", ".join(
            f"{c.change_type} {c.symbol_type} '{c.symbol_name}' in {c.file_path}"
            for c in all_changes[:25]
        )
        ctx.log(
            self.name,
            f"Detected {len(all_changes)} changed symbol(s) across {len(ctx.commit.files)} file(s), "
            f"grouped into {len(ctx.change_groups)} independent change group(s). {summary}",
        )
        return ctx
