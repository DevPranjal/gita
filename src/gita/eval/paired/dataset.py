from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen

from .common import DATASET_SHA, SEED, digest, git, read_json, write_json

PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)$")
SHA = re.compile(r"[a-f0-9]{40}$")


def freeze(source: Path, output: Path) -> dict:
    records = read_json(source)
    strata: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for row in records:
        match = PR_URL.fullmatch(row["githubPrUrl"])
        if not match or not SHA.fullmatch(row["source_commit"]) or not SHA.fullmatch(row["target_commit"]):
            raise ValueError("Invalid benchmark repository or revision")
        identity = (row["githubPrUrl"], row["source_commit"], row["target_commit"])
        if identity in seen:
            raise ValueError(f"Duplicate benchmark instance: {identity}")
        seen.add(identity)
        task = {
            "id": digest(identity)[:16], "repo": match[1], "pr_url": row["githubPrUrl"],
            "base": row["source_commit"], "head": row["target_commit"],
            "language": row["project_main_language"], "change_lines": row["change_line_count"],
            "reference_count": len(row["comments"]), "reference_digest": digest(row["comments"]),
        }
        strata[task["language"]].append(task)
    tasks = []
    for language in sorted(strata):
        ordered = sorted(strata[language], key=lambda t: digest([SEED, t["id"]]))
        for i, task in enumerate(ordered):
            task["split"] = "pilot" if i == 0 else "confirmation"
            task["stratum_rank"] = i
            tasks.append(task)
    # Round robin across languages, avoiding a language/time/cache confound.
    tasks.sort(key=lambda t: (t["stratum_rank"], t["language"]))
    manifest = {
        "dataset": "alibaba/aacr-bench", "dataset_commit": DATASET_SHA,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "seed": SEED, "selection": "all positive-sample PRs; first hash per language is pilot",
        "tasks": tasks, "languages": dict(Counter(t["language"] for t in tasks)),
    }
    manifest["manifest_digest"] = digest(manifest)
    if output.exists() and read_json(output) != manifest:
        raise ValueError("Refusing to overwrite a different frozen manifest")
    write_json(output, manifest)
    return manifest


def resolve_scope(task: dict, repo: Path) -> dict:
    scope_path = repo / "scope.json"
    if scope_path.exists():
        scope = read_json(scope_path)
        if scope["supplied_base"] != task["base"] or scope["head"] != task["head"]:
            raise ValueError("Cached revision scope mismatch")
    else:
        comparison = subprocess.run(
            ["gh", "api", f"repos/{task['repo']}/compare/{task['base']}...{task['head']}",
             "--jq", ".merge_base_commit.sha"],
            capture_output=True, timeout=120, check=False)
        merge_base = comparison.stdout.decode("utf8").strip()
        if comparison.returncode or not SHA.fullmatch(merge_base):
            raise RuntimeError(f"Cannot resolve pinned merge base: {comparison.stderr.decode('utf8', 'replace')}")
        scope = {"supplied_base": task["base"], "head": task["head"],
                 "base": merge_base, "base_adjusted": merge_base != task["base"]}
        write_json(scope_path, scope)
    return scope


def prepare(task: dict, root: Path) -> tuple[Path, dict]:
    """Fetch exactly the named trees, no PR comments and no future branch tips."""
    repo = root / task["id"]
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    expected_url = f"https://github.com/{task['repo']}.git"
    remotes = git(repo, "remote").splitlines()
    if "origin" not in remotes:
        git(repo, "remote", "add", "origin", expected_url)
    elif git(repo, "remote", "get-url", "origin").strip() != expected_url:
        raise ValueError("Repository cache origin mismatch")
    scope_path = repo / "scope.json"
    scope = resolve_scope(task, repo)
    for rev in dict.fromkeys((task["base"], task["head"], scope["base"])):
        present = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}^{{commit}}"],
                                 capture_output=True, check=False)
        if present.returncode:
            git(repo, "-c", "protocol.file.allow=never", "fetch", "--quiet", "--no-tags",
                "--depth=1", "origin", rev, timeout=600)
        if git(repo, "rev-parse", f"{rev}^{{commit}}").strip() != rev:
            raise ValueError("Fetched revision mismatch")
    # Set HEAD without checking out untrusted source (e.g. symlinks, hooks).
    git(repo, "update-ref", "HEAD", task["head"])
    if not git(repo, "diff", "--name-only", scope["base"], task["head"]).strip():
        raise ValueError("Benchmark revisions have an empty diff")
    scope["diff_shortstat"] = git(repo, "diff", "--shortstat", scope["base"], task["head"]).strip()
    scope["changed_files"] = git(repo, "diff", "--name-only", scope["base"], task["head"]).splitlines()
    write_json(scope_path, scope)
    return repo, scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        args.source.parent.mkdir(parents=True, exist_ok=True)
        url = (f"https://raw.githubusercontent.com/alibaba/aacr-bench/{DATASET_SHA}"
               "/dataset/positive_samples.json")
        with urlopen(url, timeout=60) as response:
            args.source.write_bytes(response.read())
    manifest = freeze(args.source, args.manifest)
    print(f"Frozen {len(manifest['tasks'])} tasks: {manifest['languages']}", flush=True)


if __name__ == "__main__":
    main()
