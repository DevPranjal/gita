"""Task manifest for the evaluation.

Ground truth is hand-curated from commit messages and git's own hunk headers.
It never comes from gita: a benchmark scored by the tool under test is worthless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(slots=True)
class Setup:
    """An edit applied before the run, to create uncommitted work."""

    file: str
    append: str = ""


@dataclass(slots=True)
class Task:
    id: str
    repo: str
    prompt: str
    must_mention: list[str]
    base: str = "HEAD^"
    head: str = "HEAD"
    category: str = "review"
    note: str = ""
    setup: Setup | None = None
    expect_no_benefit: bool = False

    @property
    def revisions(self) -> str:
        return f"{self.base}..{self.head}"


def _to_task(raw: dict) -> Task:
    if not raw.get("must_mention"):
        raise ValueError(f"task {raw.get('id')!r} has no must_mention ground truth")

    setup = raw.get("setup")
    return Task(
        id=raw["id"],
        repo=raw["repo"],
        prompt=raw["prompt"],
        must_mention=list(raw["must_mention"]),
        base=raw.get("base", "HEAD^"),
        head=raw.get("head", "HEAD"),
        category=raw.get("category", "review"),
        note=raw.get("note", ""),
        setup=Setup(**setup) if setup else None,
        expect_no_benefit=bool(raw.get("expect_no_benefit", False)),
    )


def load_tasks(path: str | Path) -> list[Task]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf8")) or []
    tasks = [_to_task(item) for item in raw]

    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            raise ValueError(f"duplicate task id: {task.id}")
        seen.add(task.id)
    return tasks
