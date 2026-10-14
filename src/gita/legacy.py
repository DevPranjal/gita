"""Explicit, process-local opt-in for the transitional structural-review product."""

from functools import wraps
import os

FLAG = "GITA_ENABLE_LEGACY"


class LegacyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def legacy_enabled() -> bool:
    value = os.environ.get(FLAG)
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise LegacyError("invalid_configuration",
                      f"{FLAG} must be unset, 0 (disabled), or 1 (enabled); got {value!r}.")


def require_legacy(operation: str) -> None:
    if not legacy_enabled():
        raise LegacyError(
            "legacy_disabled",
            f"{operation} is a retired legacy operation. Set {FLAG}=1 to opt in temporarily. "
            "Use gita index, gita id and gita history for published indexed evidence. "
            "History/events are not endpoint diffs; no indexed endpoint diff is substituted.",
        )


def legacy_operation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        require_legacy(f"{function.__module__}.{function.__name__}")
        return function(*args, **kwargs)

    return guarded
