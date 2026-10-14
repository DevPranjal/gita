"""Line-offset presentation of patches from already-selected source."""

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _shift(start: str, offset: int) -> str:
    number = int(start)
    return str(number + offset) if number else "0"


def _absolute(patch: str, old_offset: int, new_offset: int) -> str:
    """Renumber entity-relative hunk headers to file-absolute line numbers."""
    if not old_offset and not new_offset:
        return patch
    out = []
    for line in patch.splitlines(keepends=True):
        match = _HUNK.match(line)
        if match is None:
            out.append(line)
            continue
        old_start, old_len, new_start, new_len = match.groups()
        old = f"-{_shift(old_start, old_offset)}" + (f",{old_len}" if old_len else "")
        new = f"+{_shift(new_start, new_offset)}" + (f",{new_len}" if new_len else "")
        out.append(f"@@ {old} {new} @@{line[match.end():]}")
    return "".join(out)
