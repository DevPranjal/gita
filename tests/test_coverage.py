"""Coverage gaps found while auditing gita as a general-purpose git replacement:

1. working-tree diffs failed outright -- the most common agent operation
2. non-code files were skipped silently -- gita saw 1 of 3 changed files
"""

from __future__ import annotations

import subprocess

import pytest

from gita import diff_revisions, extract_path
from gita.entities.languages import is_supported
from gita.vcs.git import Repo


@pytest.fixture
def mixed(tmp_path):
    """A repo of code, docs and config, with uncommitted edits pending."""

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "notes.md").write_text("# Title\n\nintro\n\n## Setup\n\nsteps\n")
    (tmp_path / "conf.yaml").write_text("service:\n  port: 80\n  name: web\n")
    (tmp_path / "pkg.json").write_text('{"name": "x", "version": "1.0.0"}\n')
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\nRUN pip install x\n")
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    return Repo(tmp_path)


def edit(repo):
    root = repo.root
    (root / "a.py").write_text("def f():\n    return 2\n")
    (root / "notes.md").write_text("# Title\n\nintro\n\n## Setup\n\nrewritten\n")
    (root / "conf.yaml").write_text("service:\n  port: 8080\n  name: web\n")
    (root / "pkg.json").write_text('{"name": "x", "version": "2.0.0"}\n')
    (root / "Dockerfile").write_text("FROM python:3.12\nRUN pip install x\n")


class TestWorkingTree:
    def test_uncommitted_changes_are_visible(self, mixed):
        edit(mixed)
        changeset = diff_revisions(mixed, "HEAD", None)
        assert any("a.py::f" in c.entity.id for c in changeset.material())

    def test_clean_tree_reports_nothing(self, mixed):
        assert diff_revisions(mixed, "HEAD", None).material() == []

    def test_staged_only_view(self, mixed):
        edit(mixed)
        subprocess.run(["git", "-C", str(mixed.root), "add", "a.py"], check=True,
                       capture_output=True)
        staged = diff_revisions(mixed, "HEAD", "STAGED")
        assert {c.entity.path for c in staged.material()} == {"a.py"}

    def test_new_untracked_file_is_included(self, mixed):
        (mixed.root / "new.py").write_text("def brand_new():\n    return 1\n")
        subprocess.run(["git", "-C", str(mixed.root), "add", "new.py"], check=True,
                       capture_output=True)
        changeset = diff_revisions(mixed, "HEAD", None)
        assert any("brand_new" in c.entity.id for c in changeset.material())


class TestNonCodeFiles:
    def test_every_changed_file_is_accounted_for(self, mixed):
        edit(mixed)
        changeset = diff_revisions(mixed, "HEAD", None)
        seen = {c.entity.path for c in changeset.material()}
        assert seen == {"a.py", "notes.md", "conf.yaml", "pkg.json", "Dockerfile"}

    def test_markdown_sections_become_entities(self, mixed):
        tree = extract_path(b"# Title\n\nintro\n\n## Setup\n\nsteps\n", "notes.md")
        ids = {e.id for e in tree.walk()}
        assert "notes.md::Title" in ids
        assert "notes.md::Title::Setup" in ids

    def test_markdown_edit_is_attributed_to_its_section(self, mixed):
        edit(mixed)
        changed = {c.entity.id for c in diff_revisions(mixed, "HEAD", None).material()}
        assert "notes.md::Title::Setup" in changed

    def test_yaml_keys_become_entities(self):
        ids = {e.id for e in extract_path(b"service:\n  port: 80\n", "c.yaml").walk()}
        assert "c.yaml::service" in ids
        assert "c.yaml::service::port" in ids

    def test_yaml_value_change_is_attributed_to_the_key(self, mixed):
        edit(mixed)
        changed = {c.entity.id for c in diff_revisions(mixed, "HEAD", None).material()}
        assert "conf.yaml::service::port" in changed

    def test_json_keys_become_entities(self):
        ids = {e.id for e in extract_path(b'{"name": "x", "v": 1}', "p.json").walk()}
        assert "p.json::name" in ids

    def test_unknown_file_type_still_reports_a_change(self, mixed):
        edit(mixed)
        changed = {c.entity.id for c in diff_revisions(mixed, "HEAD", None).material()}
        assert "Dockerfile" in changed

    def test_binary_files_are_skipped_not_mangled(self, mixed):
        (mixed.root / "blob.bin").write_bytes(bytes(range(256)) * 40)
        subprocess.run(["git", "-C", str(mixed.root), "add", "-A"], check=True,
                       capture_output=True)
        changeset = diff_revisions(mixed, "HEAD", None)
        assert not any(c.entity.path == "blob.bin" for c in changeset.material())

    def test_supported_covers_docs_and_config(self):
        for path in ("a.md", "b.yaml", "c.yml", "d.json", "e.toml"):
            assert is_supported(path), path
