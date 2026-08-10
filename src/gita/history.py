"""Series-of-events view over a range of commits.

A diff between two revisions is cumulative: it says where the code ended up, not
how it got there. Answering "when did this behaviour actually change" needs the
per-commit narrative, so this walks commits and diffs each against its parent.

Cost is linear in the number of commits, so callers pass a limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .diff.changes import ChangeKind, EntityChange
from .context.resolve import resolve_entity
from .revisions import diff_revisions
from .vcs.git import Repo

DEFAULT_LIMIT = 20

_FORMAT = "%H%x1f%s%x1f%aI"


@dataclass(slots=True)
class CommitSummary:
    sha: str
    subject: str
    date: str
    changes: list[EntityChange] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.sha[:10]

    def material(self) -> list[EntityChange]:
        return [c for c in self.changes if not c.is_noise]


@dataclass(slots=True)
class EntityEvent:
    sha: str
    subject: str
    date: str
    entity_id: str
    kind: ChangeKind

    @property
    def short(self) -> str:
        return self.sha[:10]

    def __str__(self) -> str:
        return f"{self.short}  {self.date[:10]}  {self.kind.value:<18}{self.subject}"


def commits(repo: Repo, since: str | None = None, until: str = "HEAD",
            limit: int = DEFAULT_LIMIT) -> list[tuple[str, str, str]]:
    """Newest-first ``(sha, subject, iso_date)`` triples."""
    span = f"{since}..{until}" if since else until
    raw = repo.text("log", f"--format={_FORMAT}", f"-n{limit}", span, check=False)

    out = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


def series(repo: str | Path | Repo, since: str | None = None, until: str = "HEAD",
           limit: int = DEFAULT_LIMIT) -> list[CommitSummary]:
    """Per-commit entity changes, newest first."""
    repo = repo if isinstance(repo, Repo) else Repo(repo)

    summaries = []
    for sha, subject, date in commits(repo, since, until, limit):
        changeset = diff_revisions(repo, repo.base_of(sha), sha)
        summaries.append(CommitSummary(sha=sha, subject=subject, date=date,
                                       changes=changeset.material()))
    return summaries


def entity_history(repo: str | Path | Repo, entity_id: str,
                   since: str | None = None, until: str = "HEAD",
                   limit: int = DEFAULT_LIMIT) -> list[EntityEvent]:
    """How one entity changed across a range, newest first.

    ``entity_id`` may be a bare name: agents type `fetch`, not
    `svc.py::fetch`, and demanding the qualified form cost a wasted turn.

    Commits that left the entity alone are omitted -- the point is the sequence
    of real changes, not a list of every commit.
    """
    summaries = series(repo, since, until, limit)

    seen = {c.entity.id for s in summaries for c in s.changes}
    resolved = entity_id if entity_id in seen else resolve_entity(seen, entity_id)
    if resolved is None:
        return []

    events = []
    for summary in summaries:
        for change in summary.changes:
            if change.entity.id != resolved:
                continue
            events.append(EntityEvent(
                sha=summary.sha,
                subject=summary.subject,
                date=summary.date,
                entity_id=resolved,
                kind=change.kind,
            ))
    return events
