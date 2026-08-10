"""Integration: run the engine over real commits from the Spike A corpus.

Unit tests prove the logic; this proves it survives real history. Skipped when
the corpus has not been cloned (it is gitignored -- see docs/SCOPE.md section 8).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gita import ChangeKind, diff_revisions
from gita.vcs.git import Repo

CORPUS = Path(__file__).resolve().parents[1] / "spikes" / "attribution" / "corpus"
REPOS = ["express", "flask", "gin", "got", "ripgrep"]

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not cloned")


def recent_pairs(repo: Repo, limit: int = 5) -> list[tuple[str, str]]:
    shas = repo.text("log", "--no-merges", "--format=%H", f"-n{limit}").split()
    return [(f"{sha}^", sha) for sha in shas]


@pytest.fixture(params=REPOS)
def repo(request):
    path = CORPUS / request.param
    if not (path / ".git").exists():
        pytest.skip(f"{request.param} not cloned")
    return Repo(path)


def test_engine_survives_real_history(repo):
    total = 0
    for base, head in recent_pairs(repo):
        changeset = diff_revisions(repo, base, head)
        total += len(changeset)
        assert changeset.parse_errors <= changeset.files_changed
    assert total > 0, "expected at least one entity change across five commits"


def test_entity_ids_are_stable_across_extractions(repo):
    base, head = recent_pairs(repo, 1)[0]
    first = diff_revisions(repo, base, head)
    second = diff_revisions(repo, base, head)
    assert [c.id for c in first] == [c.id for c in second]
    assert first.counts() == second.counts()


def test_noise_is_separable_from_material_change(repo):
    for base, head in recent_pairs(repo):
        changeset = diff_revisions(repo, base, head)
        material = changeset.material()
        assert len(material) <= len(changeset)
        assert all(c.kind not in (ChangeKind.UNCHANGED, ChangeKind.COSMETIC)
                   for c in material)


def test_interface_changes_are_a_subset_of_material(repo):
    for base, head in recent_pairs(repo):
        changeset = diff_revisions(repo, base, head)
        material_ids = {id(c) for c in changeset.material()}
        for change in changeset.interface_changes():
            assert id(change) in material_ids
