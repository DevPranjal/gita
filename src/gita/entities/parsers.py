"""Share an explicitly selected grammar cache across CLI, MCP and parse workers."""

import os
from pathlib import Path

import tree_sitter_language_pack as language_pack
from tree_sitter_language_pack import get_parser as get_parser


_cache = os.environ.get("GITA_PARSER_CACHE")
if _cache is not None:
    if not Path(_cache).is_absolute():
        raise ValueError("GITA_PARSER_CACHE must be an absolute grammar-cache directory")
    language_pack.configure(language_pack.PackConfig(cache_dir=_cache))
