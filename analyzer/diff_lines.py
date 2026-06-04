"""Map LLM line numbers onto unified-diff line coordinates."""
import re
from typing import List, Dict, Any


def valid_new_file_lines(patch: str) -> list[int]:
    """Line numbers in the post-change file that appear in this patch."""
    lines: list[int] = []
    current = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if match:
                current = int(match.group(1)) - 1
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+") or raw.startswith(" "):
            current += 1
            lines.append(current)
    return lines


def resolve_line_in_patch(patch: str, line_start: int) -> int | None:
    """Snap LLM line_start to a line that exists in the diff (for GitHub inline comments)."""
    valid = valid_new_file_lines(patch)
    if not valid:
        return None
    if line_start in valid:
        return line_start
    return min(valid, key=lambda n: abs(n - int(line_start)))


def align_issues_to_patch(patch: str, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adjust issue line numbers so they anchor to diff-visible lines."""
    if not patch or not issues:
        return issues

    aligned = []
    for item in issues:
        row = dict(item)
        raw_line = row.get("line_start") or row.get("line")
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            aligned.append(row)
            continue

        resolved = resolve_line_in_patch(patch, line)
        if resolved is not None:
            row["line_start"] = resolved
            row["line_end"] = max(resolved, int(row.get("line_end") or resolved))
        aligned.append(row)
    return aligned
