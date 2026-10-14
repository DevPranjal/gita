"""gita — git reimagined for a world of agent coders."""

from importlib import import_module

__version__ = "1.0.0"

_LEGACY_EXPORTS = {
    **dict.fromkeys(("ChangeKind", "ChangeSet", "EntityChange"), ".diff.changes"),
    **dict.fromkeys(("diff_files", "diff_trees", "similarity"), ".diff.differ"),
    **dict.fromkeys(("extract", "extract_path"), ".entities.extractor"),
    **dict.fromkeys(("for_path", "is_supported"), ".entities.languages"),
    **dict.fromkeys(("Entity", "EntityKind", "EntityTree"), ".entities.model"),
    "diff_revisions": ".revisions",
}


def __getattr__(name):
    from inspect import isfunction
    from .legacy import legacy_enabled, legacy_operation, require_legacy

    if name == "__all__":
        return ["__version__", *(_LEGACY_EXPORTS if legacy_enabled() else ())]
    if name not in _LEGACY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    require_legacy(f"gita.{name}")
    # Do not cache on the package: subsequent access must recheck the flag.
    value = getattr(import_module(_LEGACY_EXPORTS[name], __name__), name)
    return legacy_operation(value) if isfunction(value) else value
