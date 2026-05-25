"""Repository operations: init / add / commit / log / merge.

A :class:`Repo` is a thin wrapper around a ``.gitpp`` directory. State lives
on disk; nothing is cached in-process beyond the path itself.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import libcst as cst

from . import objects as obj
from .manifest import build_manifest, diff_sources
from .merge import Conflict, merge_modules


DEFAULT_BRANCH = "main"


# ---------------------------------------------------------------------------
# repo discovery + init
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    root: Path

    @property
    def gitpp(self) -> Path:
        return self.root / ".gitpp"

    @property
    def head_file(self) -> Path:
        return self.gitpp / "HEAD"

    @property
    def index_file(self) -> Path:
        return self.gitpp / "index"

    # --- discovery ---

    @classmethod
    def discover(cls, start: Path) -> "Repo":
        start = start.resolve()
        for d in (start, *start.parents):
            if (d / ".gitpp").is_dir():
                return cls(d)
        raise FileNotFoundError(f"no .gitpp/ found at or above {start}")

    @classmethod
    def init(cls, root: Path) -> "Repo":
        root = root.resolve()
        gitpp = root / ".gitpp"
        if gitpp.exists():
            raise FileExistsError(f"already a gitpp repo: {gitpp}")
        (gitpp / "objects").mkdir(parents=True)
        (gitpp / "refs" / "heads").mkdir(parents=True)
        (gitpp / "HEAD").write_text(f"ref: refs/heads/{DEFAULT_BRANCH}\n", encoding="utf-8")
        (gitpp / "index").write_text("{}", encoding="utf-8")
        return cls(root)

    # --- HEAD / refs ---

    def _read_head(self) -> tuple[str | None, str | None]:
        """Return (branch_name, commit_sha). Either may be None."""
        if not self.head_file.exists():
            return (None, None)
        raw = self.head_file.read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            ref = raw[len("ref: "):]
            branch = ref.rsplit("/", 1)[-1]
            ref_path = self.gitpp / ref
            sha = ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else None
            return (branch, sha)
        # detached: HEAD holds a sha directly
        return (None, raw)

    def head_commit(self) -> str | None:
        return self._read_head()[1]

    def current_branch(self) -> str | None:
        return self._read_head()[0]

    def _write_branch(self, branch: str, sha: str) -> None:
        ref_path = self.gitpp / "refs" / "heads" / branch
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(sha + "\n", encoding="utf-8")

    def resolve_ref(self, name: str) -> str:
        """Resolve a branch name or full sha to a commit sha."""
        ref_path = self.gitpp / "refs" / "heads" / name
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        # treat as raw sha if it points at an existing object
        if (self.gitpp / "objects" / name[:2] / name[2:]).exists():
            return name
        raise KeyError(f"unknown ref: {name}")

    # --- index ---

    def read_index(self) -> dict[str, str]:
        return json.loads(self.index_file.read_text(encoding="utf-8"))

    def write_index(self, index: dict[str, str]) -> None:
        self.index_file.write_text(
            json.dumps(index, sort_keys=True, indent=2), encoding="utf-8"
        )

    # --- core ops ---

    def add(self, path: Path) -> str:
        """Stage a single Python file. Returns the stored file-object sha."""
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        rel = path.relative_to(self.root).as_posix()
        source = path.read_text(encoding="utf-8")
        # Parse-check: we only accept files libcst can parse, so merges will
        # never blow up on a syntactically broken commit.
        cst.parse_module(source)
        sha = obj.write_object(self.root, obj.make_file(source))
        index = self.read_index()
        index[rel] = sha
        self.write_index(index)
        return sha

    def commit(self, message: str, timestamp: int | None = None) -> str:
        index = self.read_index()
        if not index:
            raise ValueError("nothing to commit (empty index)")
        tree_sha = obj.write_object(self.root, obj.make_tree(index))
        parent = self.head_commit()
        parents = [parent] if parent else []
        ts = int(timestamp if timestamp is not None else time.time())

        # Compute and persist the structural manifest BEFORE writing the
        # commit, so the commit object can reference it. This is the
        # "intent-rich commit" pillar: the change as ops, stored once at
        # commit time so agents never have to re-derive it.
        manifest = self._compute_manifest(parent, index)
        manifest_sha = obj.write_object(self.root, obj.make_manifest(manifest))

        commit_sha = obj.write_object(
            self.root,
            obj.make_commit(tree_sha, parents, message, ts, manifest=manifest_sha),
        )
        branch = self.current_branch() or DEFAULT_BRANCH
        self._write_branch(branch, commit_sha)
        # If HEAD was detached or missing, point it at the branch we just wrote.
        self.head_file.write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
        return commit_sha

    def log(self) -> list[dict]:
        """Return commits from HEAD walking parents (newest first).

        v0.0 follows only the first parent, since we don't create merge
        commits yet. Each entry is the raw commit object with ``sha`` added.
        """
        out: list[dict] = []
        sha = self.head_commit()
        while sha:
            c = obj.read_object(self.root, sha)
            out.append({"sha": sha, **c})
            sha = c["parents"][0] if c["parents"] else None
        return out

    # --- merge ---

    def _tree_of(self, commit_sha: str) -> dict[str, str]:
        c = obj.read_object(self.root, commit_sha)
        t = obj.read_object(self.root, c["tree"])
        return dict(t["entries"])

    def _file_source(self, file_sha: str) -> str:
        f = obj.read_object(self.root, file_sha)
        return f["source"]

    def _ancestors(self, sha: str) -> dict[str, int]:
        """Map ancestor_sha -> depth (BFS from sha). Includes sha itself at 0."""
        seen: dict[str, int] = {}
        frontier = [(sha, 0)]
        while frontier:
            s, d = frontier.pop()
            if s in seen and seen[s] <= d:
                continue
            seen[s] = d
            c = obj.read_object(self.root, s)
            for p in c["parents"]:
                frontier.append((p, d + 1))
        return seen

    def merge_base(self, a: str, b: str) -> str | None:
        """Lowest common ancestor by minimum max-depth (simple but adequate
        for v0.0 linear history with at most one merge point)."""
        anc_a = self._ancestors(a)
        anc_b = self._ancestors(b)
        common = set(anc_a) & set(anc_b)
        if not common:
            return None
        return min(common, key=lambda s: max(anc_a[s], anc_b[s]))

    def merge(self, other_ref: str, message: str | None = None) -> "MergeResult":
        """Three-way merge ``other_ref`` into HEAD.

        Returns a :class:`MergeResult`. On success, writes file objects, a new
        tree, a new merge commit (two parents), advances the current branch,
        and updates the working tree files. On conflict, writes nothing and
        leaves the working tree alone.
        """
        ours_sha = self.head_commit()
        if ours_sha is None:
            raise ValueError("HEAD has no commits; nothing to merge into")
        theirs_sha = self.resolve_ref(other_ref)
        base_sha = self.merge_base(ours_sha, theirs_sha)
        if base_sha is None:
            raise ValueError("no common ancestor")

        if base_sha == theirs_sha:
            return MergeResult(status="up-to-date", commit=ours_sha, files={}, conflicts=[])
        if base_sha == ours_sha:
            # fast-forward: just move the branch pointer
            branch = self.current_branch() or DEFAULT_BRANCH
            self._write_branch(branch, theirs_sha)
            self._checkout_tree(self._tree_of(theirs_sha))
            return MergeResult(
                status="fast-forward", commit=theirs_sha, files={}, conflicts=[]
            )

        base_tree = self._tree_of(base_sha)
        ours_tree = self._tree_of(ours_sha)
        theirs_tree = self._tree_of(theirs_sha)

        all_paths = set(base_tree) | set(ours_tree) | set(theirs_tree)
        merged_entries: dict[str, str] = {}
        per_file_conflicts: list[tuple[str, list[Conflict]]] = []
        working_writes: dict[str, str] = {}  # path -> source (to apply on success)

        for path in sorted(all_paths):
            b_src = self._file_source(base_tree[path]) if path in base_tree else None
            o_src = self._file_source(ours_tree[path]) if path in ours_tree else None
            t_src = self._file_source(theirs_tree[path]) if path in theirs_tree else None

            outcome, merged_src, conflicts = _merge_one_file(b_src, o_src, t_src)
            if conflicts:
                per_file_conflicts.append((path, conflicts))
                continue
            if outcome == "deleted":
                # Skip from tree; remove from working dir on apply.
                working_writes[path] = ""  # sentinel handled below
                continue
            assert merged_src is not None
            sha = obj.write_object(self.root, obj.make_file(merged_src))
            merged_entries[path] = sha
            working_writes[path] = merged_src

        if per_file_conflicts:
            return MergeResult(
                status="conflict",
                commit=None,
                files={p: s for p, s in merged_entries.items()},
                conflicts=per_file_conflicts,
            )

        # Apply: write files, write tree, write commit, advance branch.
        for path, src in working_writes.items():
            target = self.root / path
            if src == "" and path not in merged_entries:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(src, encoding="utf-8")

        tree_sha = obj.write_object(self.root, obj.make_tree(merged_entries))
        commit_sha = obj.write_object(
            self.root,
            obj.make_commit(
                tree_sha,
                [ours_sha, theirs_sha],
                message or f"Merge {other_ref}",
                int(time.time()),
            ),
        )
        branch = self.current_branch() or DEFAULT_BRANCH
        self._write_branch(branch, commit_sha)
        # Update index to match merged tree.
        self.write_index(merged_entries)
        return MergeResult(
            status="merged", commit=commit_sha, files=merged_entries, conflicts=[]
        )

    def _checkout_tree(self, entries: dict[str, str]) -> None:
        for path, sha in entries.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._file_source(sha), encoding="utf-8")
        self.write_index(entries)

    # --- manifest / diff ---

    def _compute_manifest(
        self, parent_sha: str | None, new_index: dict[str, str]
    ) -> dict[str, Any]:
        """Build the manifest for the commit we're about to create.

        Compares ``parent_sha``'s tree to ``new_index`` (the staged tree).
        Used internally by :meth:`commit`; also reusable by ``gitpp diff``.
        """
        prev_tree = self._tree_of(parent_sha) if parent_sha else {}
        return self._diff_trees(prev_tree, dict(new_index), parent_sha, None)

    def diff_commits(
        self, from_sha: str | None, to_sha: str | None
    ) -> dict[str, Any]:
        """Manifest between two commits. ``None`` means empty tree / working tree.

        For ``to_sha=None`` we diff against the current index (what would be
        committed next) — that's what ``gitpp diff`` shows by default.
        """
        prev_tree = self._tree_of(from_sha) if from_sha else {}
        if to_sha is None:
            curr_tree = self.read_index()
        else:
            curr_tree = self._tree_of(to_sha)
        return self._diff_trees(prev_tree, curr_tree, from_sha, to_sha)

    def _diff_trees(
        self,
        prev_tree: dict[str, str],
        curr_tree: dict[str, str],
        from_sha: str | None,
        to_sha: str | None,
    ) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for path in sorted(set(prev_tree) | set(curr_tree)):
            prev_src = self._file_source(prev_tree[path]) if path in prev_tree else None
            curr_src = self._file_source(curr_tree[path]) if path in curr_tree else None
            files.append(diff_sources(prev_src, curr_src, path=path))
        return build_manifest(files, from_sha=from_sha, to_sha=to_sha)

    def read_manifest(self, commit_sha: str) -> dict[str, Any] | None:
        """Return the manifest stored alongside a commit, or ``None`` if absent.

        Commits made before manifests existed (or by an older gitpp) won't
        have one; in that case the caller can fall back to recomputing via
        :meth:`diff_commits`.
        """
        c = obj.read_object(self.root, commit_sha)
        mref = c.get("manifest")
        if not mref:
            return None
        return obj.read_object(self.root, mref)

    # --- pillar 3: queryable history -----------------------------------

    def walk_history(self):
        """Yield ``(sha, commit_obj)`` from HEAD walking first-parent."""
        sha = self.head_commit()
        while sha:
            c = obj.read_object(self.root, sha)
            yield sha, c
            sha = c["parents"][0] if c["parents"] else None

    def symbol_log(self, name: str) -> list[dict[str, Any]]:
        """Commits whose manifest mentions ``name`` (newest first).

        Matches ``op.name``, ``op.from``, and ``op.to`` across every file
        entry. For commits without a stored manifest, recomputes via
        :meth:`diff_commits` (parent → self).
        """
        out: list[dict[str, Any]] = []
        for sha, commit in self.walk_history():
            manifest = self.read_manifest(sha)
            if manifest is None:
                parent = commit["parents"][0] if commit["parents"] else None
                manifest = self.diff_commits(parent, sha)
            touching = _manifest_ops_touching(manifest, name)
            if touching:
                out.append(
                    {
                        "sha": sha,
                        "message": commit.get("message", ""),
                        "ops": touching,
                    }
                )
        return out

    def find_callers(self, name: str, ref: str | None = None) -> list[dict[str, str]]:
        """Call sites of ``name`` in the tree at ``ref`` (default HEAD).

        Returns a list of ``{file, caller}`` records. ``caller`` is the
        enclosing top-level def/class name, or ``"<module>"`` if the call
        sits at module scope. Matches ``name(...)`` and ``x.name(...)``
        within a single file's CST.
        """
        target_sha = self.resolve_ref(ref) if ref else self.head_commit()
        if target_sha is None:
            return []
        tree = self._tree_of(target_sha)
        hits: list[dict[str, str]] = []
        for path in sorted(tree):
            if not path.endswith(".py"):
                continue
            src = self._file_source(tree[path])
            try:
                module = cst.parse_module(src)
            except cst.ParserSyntaxError:
                continue
            for caller in _walk_calls(module, name):
                hits.append({"file": path, "caller": caller})
        return hits


def _manifest_ops_touching(manifest: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Filter manifest ops down to those that mention ``name``."""
    out: list[dict[str, Any]] = []
    for file_entry in manifest.get("files", []):
        for op in file_entry.get("ops", []):
            if op.get("name") == name or op.get("from") == name or op.get("to") == name:
                out.append({"path": file_entry["path"], **op})
    return out


def _walk_calls(module: cst.Module, name: str) -> list[str]:
    """Return enclosing-symbol names for every ``name(...)``/``x.name(...)`` call.

    Single pass over the CST. We track a small scope stack of the current
    top-level function/class so each hit reports a meaningful caller.
    """
    hits: list[str] = []
    scope: list[str] = []

    class Visitor(cst.CSTVisitor):
        def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
            scope.append(node.name.value)

        def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
            scope.pop()

        def visit_ClassDef(self, node: cst.ClassDef) -> None:
            scope.append(node.name.value)

        def leave_ClassDef(self, node: cst.ClassDef) -> None:
            scope.pop()

        def visit_Call(self, node: cst.Call) -> None:
            func = node.func
            matched = False
            if isinstance(func, cst.Name) and func.value == name:
                matched = True
            elif isinstance(func, cst.Attribute) and func.attr.value == name:
                matched = True
            if matched:
                hits.append(scope[-1] if scope else "<module>")

    module.visit(Visitor())
    return hits


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _merge_one_file(
    base: str | None, ours: str | None, theirs: str | None
) -> tuple[str, str | None, list[Conflict]]:
    """3-way merge a single file. Returns (outcome, merged_source, conflicts).

    Outcomes: "merged" (use merged_source), "deleted" (drop from tree).
    """
    # Add / delete bookkeeping first.
    if ours is None and theirs is None:
        return ("deleted", None, [])  # both deleted, was in base
    if base is None and ours is None:
        return ("merged", theirs, [])  # added on theirs
    if base is None and theirs is None:
        return ("merged", ours, [])  # added on ours
    if base is None:
        # added on both sides
        if ours == theirs:
            return ("merged", ours, [])
        # fall through to content merge with empty base
        base = ""
    if ours is None:
        if base == theirs:
            return ("deleted", None, [])  # they didn't touch it, we deleted
        return (
            "merged",
            None,
            [Conflict(kind="delete-vs-modify", key=("ours-deleted",), detail="")],
        )
    if theirs is None:
        if base == ours:
            return ("deleted", None, [])
        return (
            "merged",
            None,
            [Conflict(kind="delete-vs-modify", key=("theirs-deleted",), detail="")],
        )

    # Fast paths.
    if ours == theirs:
        return ("merged", ours, [])
    if base == ours:
        return ("merged", theirs, [])
    if base == theirs:
        return ("merged", ours, [])

    # Real 3-way semantic merge.
    base_mod = cst.parse_module(base)
    ours_mod = cst.parse_module(ours)
    theirs_mod = cst.parse_module(theirs)
    merged, conflicts = merge_modules(base_mod, ours_mod, theirs_mod)
    if conflicts:
        return ("merged", None, conflicts)
    return ("merged", merged.code, [])


@dataclass(frozen=True)
class MergeResult:
    status: str  # "merged" | "fast-forward" | "up-to-date" | "conflict"
    commit: str | None
    files: dict[str, str]
    conflicts: list[tuple[str, list[Conflict]]]
