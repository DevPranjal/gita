"""Source eligibility precedes indexed preparation; failures remain visible."""
from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import secrets
import time

from .common import (EvidenceBudgetError, IntegrityError, ProtocolError, digest, file_digest, git, read_json, require, require_integrity,
                     task_identity, write_json)
from .evidence import Evidence


def policy_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    files = [path for folder in ("index", "entities", "vcs")
             for path in (root / folder).rglob("*.py")]
    return digest({str(path.relative_to(root)).replace("\\", "/"): file_digest(path)
                   for path in sorted(files)})


def provision_parsers() -> dict:
    from tree_sitter_language_pack import get_parser
    from ...entities.languages import SPECS
    from ...index.evidence import _artifacts
    languages = sorted({spec.name for spec in SPECS})
    for language in languages:
        get_parser(language)
    # get_parser must materialize native artifacts, not just report a successful download.
    artifacts = _artifacts()
    require(bool(artifacts), "Native parser inventory missing")
    return {"languages": languages, "tree_sitter": version("tree-sitter"),
            "language_pack": version("tree-sitter-language-pack"),
            "native_inventory_digest": digest(repr(artifacts)),
            "policy_digest": policy_digest()}


def source_scope(task: dict, repo: Path) -> dict:
    start = time.monotonic()
    for commit in (task["base"], task["head"]):
        require(git(repo, "cat-file", "-t", commit).strip() == b"commit", "Pinned endpoint missing")
    base = git(repo, "merge-base", task["base"], task["head"]).decode().strip()
    head_chain = git(repo, "rev-list", "--first-parent", task["head"]).decode().splitlines()
    base_chain = git(repo, "rev-list", "--first-parent", base).decode().splitlines()
    prior = base_chain[:101]  # Effective base + at most 100 prior first parents.
    same = base in head_chain
    pr_chain = head_chain[:head_chain.index(base)] if same else [task["head"]]
    # Both arms get every retained parent edge, including task base where different.
    allowed = list(dict.fromkeys(list(reversed(prior)) + list(reversed(pr_chain))
                                + [task["base"], base, task["head"]]))
    graph = {}
    for commit in allowed:
        parents = git(repo, "show", "-s", "--format=%P", commit).decode().strip().split()
        graph[commit] = [p for p in parents if p in allowed]
    scope = {"task": task, "repo": str(repo.resolve()), "effective_base": base,
             "allowed_commits": allowed, "graph": graph,
             "history_ready": same, "anchor": prior[-1],
             "history_boundary": {"policy": "up_to_100_prior_first_parents",
                                  "actual_prior": len(prior) - 1,
                                  "shallow": git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true",
                                  "same_first_parent_chain": same},
             "indexes": [], "index_preparation": {"status": "not_requested"},
             "source_seconds": time.monotonic() - start}
    scope["scope_digest"] = digest({key: value for key, value in scope.items()
                                   if key not in ("repo", "source_seconds")})
    # This independently checks merge base, ancestry and exact parents.
    broker = Evidence({**scope, "arm": "B", "cursor_secret": secrets.token_hex(32)})
    broker.raw_diff()
    broker.close()
    return scope


def prepare(root: Path, task: dict, *, index: bool = False, ready_policy: str | None = None) -> dict:
    require(not index or ready_policy == policy_digest(),
            "Final index policy confirmation required before corpus indexing")
    directory = root / "prepared" / task_identity(task)
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / "source.json"
    if receipt.exists():
        scope = read_json(receipt)
        require(scope["task"] == task, "Preparation task identity mismatch")
        require(git(Path(scope["repo"]), "rev-parse", "--is-bare-repository").strip() == b"false",
                "Old bare preparation is unsupported; prepare a new normal repository")
        # Re-read actual source graph; cached readiness is never accepted just by filename.
        checked = source_scope(task, Path(scope["repo"]))
        require(checked["scope_digest"] == scope["scope_digest"], "Source preparation changed")
    else:
        repo = directory / "repo"
        if not repo.exists():
            repo.mkdir()
            git(repo, "init")
        started = time.monotonic()
        # Exact tips only. No hooks, checkout, repository code, or upstream source repository access.
        git(repo, "fetch", "--no-tags", "--depth=102", task["mirror"],
            task["base"], task["head"], timeout=300)
        git(repo, "update-ref", "HEAD", task["head"])
        scope = source_scope(task, repo)
        scope["provision_seconds"] = time.monotonic() - started
        write_json(receipt, scope)
    if index:
        indexed_path = directory / "index.json"
        if indexed_path.exists():
            indexed = read_json(indexed_path)
            require(indexed["policy_digest"] == ready_policy, "Index preparation policy mismatch")
            scope.update(indexed["indexed_fields"])
        else:
            scope = build_indexes(scope)
            write_json(indexed_path, {"policy_digest": ready_policy,
                                     "indexed_fields": {key: scope[key] for key in (
                                         "indexes", "index_preparation", "history_ready")}})
    return scope


def build_indexes(scope: dict) -> dict:
    from ...index.builder import BuildLimits, build
    from ...index.store import IndexStore
    from ...vcs.git import CommittedRepo, Repo
    start = time.monotonic()
    contexts = []
    try:
        location = IndexStore.locate(Repo(scope["repo"]))
        opener = IndexStore.open(location, read_only=False) if location.exists() else IndexStore.create(location)
        with CommittedRepo(scope["repo"]) as repository, opener as store:
            if scope["history_ready"]:
                jobs = [("rb-history", scope["task"]["head"], scope["anchor"])]
            else:
                jobs = [("rb-base", scope["effective_base"], scope["effective_base"]),
                        ("rb-head", scope["task"]["head"], scope["task"]["head"])]
            for name, tip, anchor in jobs:
                result = build(repository, store, scope=name, ref=tip, since=anchor,
                               limits=BuildLimits(workers=1))
                commits = git(Path(scope["repo"]), "rev-list", "--first-parent",
                              tip).decode().splitlines()
                commits = list(reversed(commits[:commits.index(anchor) + 1]))
                require(set(commits) <= set(scope["allowed_commits"]), "Index chain exceeds raw scope")
                contexts.append({"scope": name, "generation": result.generation,
                                 "context": result.context.id, "tip": tip, "commits": commits,
                                 "build_status": result.status})
        scope["indexes"] = contexts
        scope["index_preparation"] = {"status": "built", "seconds": time.monotonic() - start,
                                      "policy_digest": policy_digest()}
        preflight(scope)
    except (RuntimeError, ValueError, OSError) as error:
        scope["indexes"] = []
        scope["history_ready"] = False
        integrity = isinstance(error, IntegrityError) or getattr(error, "code", "") in (
            "corrupt", "invalid_source", "match_mismatch", "parse_mismatch")
        scope["index_preparation"] = {"status": "integrity_failed" if integrity else "failed", "error": str(error),
                                      "seconds": time.monotonic() - start,
                                      "policy_digest": policy_digest()}
    return scope


def preflight(scope: dict) -> dict:
    from ...index.evidence import EvidenceLimits, parse_evidence
    from ...index.model import GitOid
    require_integrity(scope["index_preparation"]["status"] != "integrity_failed",
            "Indexed source/query integrity failure blocks scientific campaign")
    evidence = Evidence({**scope, "arm": "H", "cursor_secret": secrets.token_hex(32)})
    verified = 0
    gaps = 0
    broker_budget_gaps = 0
    try:
        # Independently reparse every changed text file at both endpoints. An always-empty
        # or consistently wrong query fails; repeatability alone is not the oracle.
        paths = git(Path(scope["repo"]), "diff", "--name-only", "-z",
                    scope["effective_base"], scope["task"]["head"]).split(b"\0")
        for commit in (scope["effective_base"], scope["task"]["head"]):
            if not scope["indexes"]:
                continue
            session = evidence._index(commit)
            selected = GitOid.from_hex("sha1", commit)
            limits = dict(session.context.limits)
            parse_limits = EvidenceLimits(**{name: limits[name] for name in (
                "max_blob_bytes", "max_definitions", "max_representation_bytes")})
            for raw in paths:
                if not raw:
                    continue
                path = raw.decode("utf8", "strict")
                if path not in evidence.members(commit):
                    continue
                blob = evidence.blob(path, commit)
                independently = parse_evidence(blob, raw, limits=parse_limits)
                if independently.result.status.value != "complete":
                    gaps += 1
                    continue
                returned = []
                cursor = None
                while True:
                    page = session.entities_in(raw, at=selected, limit=1000, cursor=cursor)
                    require_integrity(type(page) is dict and type(page.get("results")) is list
                                      and type(page.get("page")) is dict,
                                      "Malformed independent indexed entity page")
                    evidence._occurrences(page, session)
                    returned.extend(page["results"])
                    cursor = page["page"]["next_cursor"]
                    if not cursor:
                        break
                expected = [(tuple(d.names), d.span.start_byte, d.span.end_byte)
                            for d in independently.result.definitions]
                actual = [(tuple(row["names"]), row["span"]["start_byte"], row["span"]["end_byte"])
                          for row in returned]
                require_integrity(sorted(actual) == sorted(expected), "Independent extraction/query conformance failed")
                try:
                    public = evidence.entities(path, commit)
                except EvidenceBudgetError:
                    broker_budget_gaps += 1
                else:
                    shown = public.get("results")
                    require_integrity(type(shown) is list and shown == returned[:len(shown)]
                                      and (bool(shown) or not returned),
                                      "Model-facing broker entity conformance failed")
                    require_integrity(bool(public.get("next_cursor")) == (len(shown) < len(returned)),
                                      "Model-facing broker hides entity continuation")
                # Cross-operation checks catch self-consistent empty/wrong state/name
                # answers even when entity/source pagination itself is correct.
                for reference in returned[:10]:
                    token = path + "::" + "::".join(reference["names"])
                    named = session.resolve(token, at=selected, limit=1000)["results"]
                    matches = [row for row in named if row.get("occurrence")
                               and row["occurrence"]["id"] == reference["id"]]
                    require_integrity(bool(matches), "Independent name/id query conformance failed")
                    state = session.state_at(matches[0]["eid"], at=selected)
                    require_integrity(state["state"] == "present" and state["occurrence"] == reference,
                            "Independent state query conformance failed")
                verified += len(returned)
        scope["index_preparation"]["broker_budget_gap_files"] = broker_budget_gaps
        return {"independent_occurrences_verified": verified, "extraction_gaps": gaps,
                "broker_budget_gap_files": broker_budget_gaps,
                "index_status": scope["index_preparation"]}
    finally:
        evidence.close()
