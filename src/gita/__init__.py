"""gita — git reimagined for a world of agent coders."""

from .diff.changes import ChangeKind, ChangeSet, EntityChange
from .diff.differ import diff_files, diff_trees, similarity
from .entities.extractor import extract, extract_path
from .entities.languages import for_path, is_supported
from .entities.model import Entity, EntityKind, EntityTree
from .revisions import diff_revisions

__version__ = "1.0.0"

__all__ = [
    "ChangeKind",
    "ChangeSet",
    "Entity",
    "EntityChange",
    "EntityKind",
    "EntityTree",
    "diff_files",
    "diff_revisions",
    "diff_trees",
    "extract",
    "extract_path",
    "for_path",
    "is_supported",
    "similarity",
]
