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

#: Grep is only a prefilter, but a very short name matches most of a repo.
MIN_NAME_LENGTH = 3

#: A name found in this many files is not selective enough to be worth pruning by.
MAX_CANDIDATE_PATHS = 20

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
            limit: int = DEFAULT_LIMIT,
            paths: list[str] | None = None) -> list[tuple[str, str, str]]:
    """Newest-first ``(sha, subject, iso_date)`` triples.

    ``paths`` is handed to git, which prunes by comparing tree hashes instead of
    walking file contents. That pruning is why `git log -- <path>` is instant.
    """
    span = f"{since}..{until}" if since else until
    limit_args = ["--", *paths] if paths else []
    raw = repo.text("log", f"--format={_FORMAT}", f"-n{limit}", span,
                    *limit_args, check=False)

    out = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


def series(repo: str | Path | Repo, since: str | None = None, until: str = "HEAD",
           limit: int = DEFAULT_LIMIT,
           paths: list[str] | None = None,
           batched: bool = True) -> list[CommitSummary]:
    """Per-commit entity changes, newest first.

    ``batched=False`` asks git once per commit instead of once in total; it
    exists so tests can prove the two agree.
    """
    repo = repo if isinstance(repo, Repo) else Repo(repo)

    if not batched:
        return [CommitSummary(sha=sha, subject=subject, date=date,
                              changes=diff_revisions(repo, repo.base_of(sha), sha,
                                                     paths=paths).material())
                for sha, subject, date in commits(repo, since, until, limit, paths)]

    summaries = []
    for record in repo.walk(since, until, limit, paths):
        changeset = diff_revisions(repo, record.parent, record.sha, paths=paths,
                                   changed=record.files)
        summaries.append(CommitSummary(sha=record.sha, subject=record.subject,
                                       date=record.date,
                                       changes=changeset.material()))
    return summaries


def paths_holding(repo: Repo, name: str, rev: str = "HEAD") -> list[str]:
    """Tracked files that mention ``name`` at ``rev``.

    A bare name carries no path, so the fast route needs one. Grep is a coarse
    filter and that is fine: it only has to be a superset of the files that could
    define the entity, because the entity diff still decides what actually changed.
    """
    if "::" in name:
        return [name.split("::", 1)[0]]
    if len(name) < MIN_NAME_LENGTH:
        return []
    raw = repo.text("grep", "-l", "-F", "--", name, rev, check=False)
    found = []
    for line in raw.splitlines():
        # `git grep <rev>` prefixes each hit with `<rev>:`.
        _, _, path = line.partition(":")
        if path.strip():
            found.append(path.strip())
    return found[:MAX_CANDIDATE_PATHS]


def entity_history(repo: str | Path | Repo, entity_id: str,
                   since: str | None = None, until: str = "HEAD",
                   limit: int = DEFAULT_LIMIT,
                   prune: bool = True) -> list[EntityEvent]:
    """How one entity changed across a range, newest first.

    ``entity_id`` may be a bare name: agents type `fetch`, not
    `svc.py::fetch`, and demanding the qualified form cost a wasted turn.

    Commits that left the entity alone are omitted -- the point is the sequence
    of real changes, not a list of every commit.

    Only the files that could hold the entity are compared. ``prune=False``
    walks everything, which exists so tests can prove the two agree.
    """
    repo = repo if isinstance(repo, Repo) else Repo(repo)

    paths = paths_holding(repo, entity_id, until) if prune else None
    if prune and not paths:
        return []

    summaries = series(repo, since, until, limit, paths)

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
