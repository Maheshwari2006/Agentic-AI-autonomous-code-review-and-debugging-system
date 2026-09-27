"""Defensive parsing of structured (JSON) responses from the model.
Models occasionally wrap JSON in markdown fences or add stray prose
despite instructions not to -- this strips common wrappers before parsing
and never raises; callers get back (data, error)."""
from __future__ import annotations

import json
import re
from typing import Optional


def parse_json_response(text: str) -> tuple:
    """Returns (parsed_dict_or_list, error_message_or_None)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass

    # last resort: find the first {...} or [...] block
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1)), None
        except json.JSONDecodeError as e:
            return None, f"Could not parse JSON even after fence/brace extraction: {e}"

    return None, "Response did not contain any parseable JSON."
