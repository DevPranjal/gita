"""
Spike A — hunk -> symbol attribution accuracy.

Question: can a deterministic tree-sitter pipeline reliably attribute diff hunks
to their enclosing named entity? Threshold for proving gita's core: >95%.

Metrics produced
----------------
coverage        % of changed lines that land inside a named entity
residue         the remainder, broken down by top-level AST node type
                (proves the remainder is imports/comments/top-level, not missed symbols)
parse_error     % of files tree-sitter failed to parse cleanly  (the real risk)
compression     raw `git diff` tokens vs L1 symbol-level rendering
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter_language_pack import get_parser

# --------------------------------------------------------------------------
# language configuration
# --------------------------------------------------------------------------

EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
}

# In JS/TS a function is a value, not a declaration. Callbacks, IIFEs and
# `const f = () => {}` carry most of the code, so they must be entities too --
# named after whatever binds them.
ANON_FUNCTION_NODES = {
    "arrow_function",
    "function_expression",
    "function",
    "generator_function",
}

# Node types that constitute a "named entity" in the gita entity model.
ENTITY_NODES = {
    "python": {"function_definition", "class_definition", "lambda"},
    "javascript": {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        *ANON_FUNCTION_NODES,
    },
    "typescript": {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "abstract_class_declaration",
        "module",
        *ANON_FUNCTION_NODES,
    },
    "go": {
        "function_declaration",
        "method_declaration",
        "type_declaration",
    },
    "rust": {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "mod_item",
        "macro_definition",
        "union_item",
    },
}
ENTITY_NODES["tsx"] = ENTITY_NODES["typescript"]

# Classification of unattributed lines. Only `suspect` counts as a real miss;
# everything else legitimately lives outside any named entity.
RESIDUE_CLASS = {
    "<blank/whitespace>": "non-semantic",
    "comment": "non-semantic",
    "line_comment": "non-semantic",
    "block_comment": "non-semantic",
    "import_statement": "declaration",
    "import_from_statement": "declaration",
    "future_import_statement": "declaration",
    "import_declaration": "declaration",
    "use_declaration": "declaration",
    "package_clause": "declaration",
    "export_statement": "declaration",
    "attribute_item": "declaration",
    "variable_declaration": "declaration",
    "lexical_declaration": "declaration",
    "var_declaration": "declaration",
    "const_declaration": "declaration",
    "const_item": "declaration",
    "static_item": "declaration",
    "extern_crate_declaration": "declaration",
    "expression_statement": "module-level code",
    "if_statement": "module-level code",
    "for_statement": "module-level code",
    "for_in_statement": "module-level code",
    "try_statement": "module-level code",
    "while_statement": "module-level code",
    "with_statement": "module-level code",
    "labeled_statement": "module-level code",
    "ERROR": "suspect",
}


def classify_residue(node_type: str) -> str:
    return RESIDUE_CLASS.get(node_type, "suspect")

# Entities whose body is itself a container; a hit on these alone is weaker
# than a hit on a leaf entity, so we track leaf-vs-container separately.
CONTAINER_NODES = {
    "class_definition",
    "class_declaration",
    "abstract_class_declaration",
    "impl_item",
    "trait_item",
    "mod_item",
    "module",
}


def _text(node) -> str:
    if node is None:
        return ""
    return node.text.decode("utf8", "replace").strip().splitlines()[0][:80]


# Wrappers to climb through when hunting for the binding site of a function value.
_TRANSPARENT = {
    "arguments", "parenthesized_expression", "sequence_expression",
    "return_statement", "array", "expression_statement", "await_expression",
    "non_null_expression", "as_expression", "satisfies_expression",
    "type_assertion", "spread_element", "ternary_expression",
}


def binding_name(node) -> str:
    """Name an anonymous function after whatever it is bound to.

    const parse = () => {}      -> parse
    { onClick: () => {} }       -> onClick
    module.exports = fn         -> module.exports
    describe('router', () => {})-> describe('router')
    """
    current, parent = node, node.parent
    for _ in range(6):
        if parent is None:
            break
        kind = parent.type

        if kind in ("variable_declarator", "public_field_definition",
                    "field_definition", "property_signature"):
            return _text(parent.child_by_field_name("name")) or "<anon>"
        if kind == "pair":
            return _text(parent.child_by_field_name("key")) or "<anon>"
        if kind == "assignment_expression":
            return _text(parent.child_by_field_name("left")) or "<anon>"
        if kind == "call_expression":
            callee = _text(parent.child_by_field_name("function")) or "call"
            args = parent.child_by_field_name("arguments")
            if args is not None:
                for arg in args.children:
                    if arg.type in ("string", "template_string"):
                        return f"{callee}({_text(arg)})"
            return f"{callee}(fn)"

        if kind not in _TRANSPARENT:
            break
        current, parent = parent, parent.parent

    return "<anon>"


def entity_name(node) -> str:
    if node.type in ANON_FUNCTION_NODES or node.type == "lambda":
        named = node.child_by_field_name("name")
        return _text(named) or binding_name(node)

    for field_name in ("name", "type", "declarator", "pattern"):
        child = node.child_by_field_name(field_name)
        if child is not None and _text(child):
            return _text(child)
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "field_identifier",
                          "property_identifier", "constant"):
            return _text(child)
    return "<anon>"


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    return result.stdout.decode("utf8", "replace")


def pick_commits(repo: Path, limit: int) -> list[tuple[str, str]]:
    """Prefer merge commits (they approximate PRs); fall back to plain commits."""
    merges = git(repo, "log", "--merges", "--format=%H", f"-n{limit}").split()
    pairs = []
    for sha in merges:
        parents = git(repo, "rev-list", "--parents", "-n1", sha).split()
        if len(parents) >= 2:
            pairs.append((parents[1], sha))
    if len(pairs) >= limit // 2:
        return pairs[:limit]

    singles = git(repo, "log", "--no-merges", "--format=%H", f"-n{limit}").split()
    return [(f"{sha}^", sha) for sha in singles][:limit]


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChange:
    path: str
    lang: str
    old_lines: set[int] = field(default_factory=set)
    new_lines: set[int] = field(default_factory=set)


def parse_diff(diff_text: str) -> dict[str, FileChange]:
    """Parse `git diff -U0` output into per-file changed line numbers."""
    files: dict[str, FileChange] = {}
    current: FileChange | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            path = raw[2:] if raw.startswith("b/") else raw
            lang = EXT_LANG.get(Path(path).suffix)
            if lang:
                current = files.setdefault(path, FileChange(path, lang))
            else:
                current = None
        elif line.startswith("@@") and current is not None:
            match = HUNK_RE.match(line)
            if not match:
                continue
            old_start, old_count, new_start, new_count = match.groups()
            old_start, new_start = int(old_start), int(new_start)
            old_count = 1 if old_count is None else int(old_count)
            new_count = 1 if new_count is None else int(new_count)
            current.old_lines.update(range(old_start, old_start + old_count))
            current.new_lines.update(range(new_start, new_start + new_count))

    return files


# --------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------

@dataclass
class Attribution:
    total: int = 0
    attributed: int = 0
    leaf_attributed: int = 0
    residue: Counter = field(default_factory=Counter)
    residue_class: Counter = field(default_factory=Counter)
    parse_errors: int = 0
    files: int = 0
    symbols: set[str] = field(default_factory=set)


def attribute_file(source: bytes, lang: str, lines: set[int],
                   path: str, stats: Attribution) -> list[tuple[str, str, str]]:
    """Map changed line numbers onto enclosing named entities."""
    parser = get_parser(lang)
    tree = parser.parse(source)
    root = tree.root_node

    stats.files += 1
    if root.has_error:
        stats.parse_errors += 1

    entity_types = ENTITY_NODES[lang]

    # innermost entity wins: deeper nodes are visited later and overwrite
    line_entity: dict[int, tuple[str, str, bool]] = {}
    top_level: dict[int, str] = {}
    first_line, last_line = min(lines), max(lines)

    def walk(node, depth: int, trail: tuple[str, ...]):
        start, end = node.start_point[0] + 1, node.end_point[0] + 1

        if depth == 1:
            for ln in range(start, end + 1):
                top_level.setdefault(ln, node.type)

        next_trail = trail
        if node.type in entity_types:
            next_trail = trail + (entity_name(node),)
            is_leaf = node.type not in CONTAINER_NODES
            for ln in range(start, end + 1):
                if ln in lines:
                    prior = line_entity.get(ln)
                    if prior is None or not prior[2] or is_leaf:
                        line_entity[ln] = ("::".join(next_trail), node.type, is_leaf)

        for child in node.children:
            if child.end_point[0] + 1 < first_line or child.start_point[0] + 1 > last_line:
                continue
            walk(child, depth + 1, next_trail)

    walk(root, 0, ())

    touched: list[tuple[str, str, str]] = []
    max_line = root.end_point[0] + 1
    for ln in lines:
        if ln > max_line:
            continue  # trailing deletion past EOF
        stats.total += 1
        hit = line_entity.get(ln)
        if hit:
            stats.attributed += 1
            if hit[2]:
                stats.leaf_attributed += 1
            stats.symbols.add(f"{path}::{hit[0]}")
            touched.append((path, hit[0], hit[1]))
        else:
            node_type = top_level.get(ln, "<blank/whitespace>")
            stats.residue[node_type] += 1
            stats.residue_class[classify_residue(node_type)] += 1

    return touched


# --------------------------------------------------------------------------
# token accounting
# --------------------------------------------------------------------------

def make_counter():
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(encoder.encode(text, disallowed_special=())), "tiktoken/cl100k_base"
    except Exception:
        return lambda text: max(1, len(text) // 4), "approx(chars/4)"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def render_l1_full(touched: list[tuple[str, str, str]]) -> str:
    """Every distinct entity, at full nesting depth."""
    return "\n".join(sorted({f"{p}::{t} [{k}]" for p, t, k in touched}))


def render_l1_rolled(touched: list[tuple[str, str, str]], depth: int = 1) -> str:
    """Entities rolled up to `depth` path segments, with nested counts.

    Test-heavy JS diffs touch hundreds of tiny callbacks; listing each one at
    full depth defeats the purpose. Rolling up keeps the signal and drops the
    enumeration -- the agent can drill via L2 if it needs the detail.
    """
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path, trail, _kind in touched:
        head = "::".join(trail.split("::")[:depth])
        groups[(path, head)].add(trail)

    lines = []
    for (path, head), trails in sorted(groups.items()):
        nested = len(trails) - 1
        suffix = f" (+{nested} nested)" if nested > 0 else ""
        lines.append(f"{path}::{head}{suffix}")
    return "\n".join(lines)


def run_repo(repo: Path, commits: int, count_tokens) -> dict:
    stats = Attribution()
    raw_tokens = 0
    l1_tokens = 0
    l1_rolled_tokens = 0
    commit_rows = []

    for base, sha in pick_commits(repo, commits):
        diff_u0 = git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", base, sha)
        if not diff_u0.strip():
            continue
        changes = parse_diff(diff_u0)
        if not changes:
            continue

        touched_all: list[tuple[str, str, str]] = []
        before = stats.total

        for path, change in list(changes.items())[:40]:
            for rev, lines in ((sha, change.new_lines), (base, change.old_lines)):
                if not lines:
                    continue
                blob = subprocess.run(
                    ["git", "-C", str(repo), "show", f"{rev}:{path}"],
                    capture_output=True, check=False,
                ).stdout
                if not blob or b"\x00" in blob[:8000]:
                    continue
                try:
                    touched_all += attribute_file(blob, change.lang, lines, path, stats)
                except RecursionError:
                    stats.parse_errors += 1

        if stats.total == before:
            continue

        raw_diff = git(repo, "diff", "--no-color", "--no-ext-diff", base, sha,
                       "--", *changes.keys())
        r_tok = count_tokens(raw_diff)
        l_tok = count_tokens(render_l1_full(touched_all))
        roll_tok = count_tokens(render_l1_rolled(touched_all))
        raw_tokens += r_tok
        l1_tokens += l_tok
        l1_rolled_tokens += roll_tok
        commit_rows.append({
            "sha": sha[:10],
            "files": len(changes),
            "lines": stats.total - before,
            "symbols": len({(p, t) for p, t, _ in touched_all}),
            "raw_tokens": r_tok,
            "l1_tokens": l_tok,
            "l1_rolled_tokens": roll_tok,
        })

    coverage = stats.attributed / stats.total if stats.total else 0.0
    suspect = stats.residue_class.get("suspect", 0)
    return {
        "repo": repo.name,
        "commits": len(commit_rows),
        "files": stats.files,
        "changed_lines": stats.total,
        "attributed": stats.attributed,
        "coverage": coverage,
        "leaf_coverage": stats.leaf_attributed / stats.total if stats.total else 0.0,
        "suspect_lines": suspect,
        "miss_rate": suspect / stats.total if stats.total else 0.0,
        "parse_error_files": stats.parse_errors,
        "parse_error_rate": stats.parse_errors / stats.files if stats.files else 0.0,
        "distinct_symbols": len(stats.symbols),
        "residue": dict(stats.residue.most_common()),
        "residue_class": dict(stats.residue_class.most_common()),
        "raw_tokens": raw_tokens,
        "l1_tokens": l1_tokens,
        "l1_rolled_tokens": l1_rolled_tokens,
        "compression": 1 - (l1_tokens / raw_tokens) if raw_tokens else 0.0,
        "compression_rolled": 1 - (l1_rolled_tokens / raw_tokens) if raw_tokens else 0.0,
        "per_commit": commit_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(Path(__file__).parent / "corpus"))
    parser.add_argument("--commits", type=int, default=20)
    parser.add_argument("--out", default=str(Path(__file__).parent / "results.json"))
    args = parser.parse_args()

    count_tokens, token_method = make_counter()
    corpus = Path(args.corpus)
    repos = sorted(p for p in corpus.iterdir() if (p / ".git").exists())
    if not repos:
        print(f"no repos found under {corpus}", file=sys.stderr)
        return 1

    results = []
    for repo in repos:
        print(f"  analysing {repo.name} ...", flush=True)
        results.append(run_repo(repo, args.commits, count_tokens))

    total_lines = sum(r["changed_lines"] for r in results)
    total_attr = sum(r["attributed"] for r in results)
    total_raw = sum(r["raw_tokens"] for r in results)
    total_l1 = sum(r["l1_tokens"] for r in results)
    total_rolled = sum(r["l1_rolled_tokens"] for r in results)
    total_files = sum(r["files"] for r in results)
    total_suspect = sum(r["suspect_lines"] for r in results)
    residue = Counter()
    residue_class = Counter()
    for r in results:
        residue.update(r["residue"])
        residue_class.update(r["residue_class"])

    summary = {
        "token_method": token_method,
        "repos": len(results),
        "commits": sum(r["commits"] for r in results),
        "files": total_files,
        "changed_lines": total_lines,
        "coverage": total_attr / total_lines if total_lines else 0.0,
        "suspect_lines": total_suspect,
        "miss_rate": total_suspect / total_lines if total_lines else 0.0,
        "parse_error_files": sum(r["parse_error_files"] for r in results),
        "parse_error_rate": (sum(r["parse_error_files"] for r in results)
                             / total_files) if total_files else 0.0,
        "raw_tokens": total_raw,
        "l1_tokens": total_l1,
        "l1_rolled_tokens": total_rolled,
        "compression": 1 - (total_l1 / total_raw) if total_raw else 0.0,
        "compression_rolled": 1 - (total_rolled / total_raw) if total_raw else 0.0,
        "residue": dict(residue.most_common(20)),
        "residue_class": dict(residue_class.most_common()),
    }

    Path(args.out).write_text(
        json.dumps({"summary": summary, "per_repo": results}, indent=2),
        encoding="utf8",
    )

    # ---- report ----
    print()
    print("=" * 78)
    print("SPIKE A  ·  hunk -> symbol attribution".center(78))
    print("=" * 78)
    print(f"{'repo':<11}{'commits':>8}{'files':>7}{'lines':>8}"
          f"{'coverage':>10}{'miss':>8}{'parse err':>10}{'compress':>10}{'rolled':>8}")
    print("-" * 78)
    for r in results:
        print(f"{r['repo']:<11}{r['commits']:>8}{r['files']:>7}{r['changed_lines']:>8}"
              f"{r['coverage']:>9.1%}{r['miss_rate']:>8.2%}{r['parse_error_rate']:>10.1%}"
              f"{r['compression']:>10.1%}{r['compression_rolled']:>8.1%}")
    print("-" * 78)
    print(f"{'TOTAL':<11}{summary['commits']:>8}{total_files:>7}"
          f"{total_lines:>8}{summary['coverage']:>9.1%}{summary['miss_rate']:>8.2%}"
          f"{summary['parse_error_rate']:>10.1%}{summary['compression']:>10.1%}"
          f"{summary['compression_rolled']:>8.1%}")
    print()
    print(f"tokens ({token_method}):")
    print(f"   raw git diff        {total_raw:>10,}")
    print(f"   L1 full depth       {total_l1:>10,}   {summary['compression']:>6.1%} reduction")
    print(f"   L1 rolled (depth 1) {total_rolled:>10,}   {summary['compression_rolled']:>6.1%} reduction")
    print()
    print("unattributed residue, classified:")
    total_residue = sum(residue_class.values()) or 1
    for label, n in residue_class.most_common():
        flag = "  <-- real miss" if label == "suspect" else ""
        print(f"   {label:<22}{n:>7}  {n / total_residue:>6.1%} of residue{flag}")
    print()
    print("residue detail by top-level node type:")
    for node_type, n in residue.most_common(10):
        print(f"   {node_type:<34}{n:>7}  ({classify_residue(node_type)})")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
