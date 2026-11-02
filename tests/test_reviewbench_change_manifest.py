"""Real-Git changed-range discovery, independent of bounded source delivery."""
from pathlib import Path
import re
import shutil
import stat
import uuid

import pytest

from gita.eval.reviewbench import initial_context as context
from gita.eval.reviewbench.common import ProtocolError, git, task_identity
from gita.eval.reviewbench.preparation import source_scope


@pytest.fixture
def changed_repo(monkeypatch):
    repository = Path(__file__).resolve().parents[1] / (".manifest-test-" + uuid.uuid4().hex)
    repository.mkdir()
    try:
        git(repository, "init")
        git(repository, "config", "user.name", "Manifest Test")
        git(repository, "config", "user.email", "manifest@example.invalid")
        (repository / "src").mkdir()
        path = "src/odd name.py"
        lines = [f"# row {i:04d} " + "x" * 70 + "\n" for i in range(1, 1401)]
        (repository / path).write_bytes("".join(lines).encode())
        (repository / "deleted.py").write_bytes(b"old one\nold two\n")
        (repository / "empty.py").write_bytes(b"old\n")
        (repository / "leading.py").write_bytes(b"one\ntwo\nthree\nfour\n")
        (repository / "binary.bin").write_bytes(b"\0old")
        git(repository, "add", ".")
        git(repository, "commit", "--no-gpg-sign", "-m", "base")
        base = git(repository, "rev-parse", "HEAD").decode().strip()
        for line in [200, *range(400, 700), 1000]:
            lines[line - 1] = lines[line - 1].replace("x", "y")
        del lines[1199:1202]
        (repository / path).write_bytes("".join(lines).encode())
        (repository / "deleted.py").unlink()
        (repository / "empty.py").write_bytes(b"")
        (repository / "leading.py").write_bytes(b"three\nfour\n")
        (repository / "added.py").write_bytes(b"new one\nnew two")
        (repository / "binary.bin").write_bytes(b"\0new")
        git(repository, "add", "-A")
        git(repository, "commit", "--no-gpg-sign", "-m", "head")
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        task = {"repo": "https://github.com/synthetic/manifest", "pr_number": 1,
                "base": base, "head": head}
        task["id"] = task_identity(task)
        scope = source_scope(task, repository)
        monkeypatch.setattr(context, "related_sources", lambda *args, **kwargs: {"sources": []})
        yield repository, scope, path
    finally:
        def writable_remove(operation, path, error):
            Path(path).chmod(stat.S_IWRITE)
            operation(path)
        shutil.rmtree(repository, onerror=writable_remove)


def build(fixture, budget=100000, indexed=False):
    return context.build_initial_context(fixture[1], indexed=indexed, max_characters=budget)


def manifest_files(packet):
    manifest = packet["changed_hunks"]
    assert manifest["row_fields"] == [
        "base_skip", "base_count", "count_delta", "head_adjust", "adjacent_line"]
    assert len(manifest["hunks"]) == len(packet["inventory"])
    assert len(manifest["preload"]) == sum(map(len, manifest["hunks"]))
    statuses = iter(manifest["preload"])
    files = {}
    for member, rows in zip(packet["inventory"], manifest["hunks"]):
        hunks, base_end, offset = [], 0, 0
        for row in rows:
            skip, removed, delta, adjust, adjacent = (
                row + manifest["row_defaults"][len(row):])
            old, count = base_end + skip, removed + delta
            start = old + offset + int(removed == 0) - int(count == 0) + adjust
            hunk = {"base": [old, removed], "head": [start, count],
                    "preload": manifest["preload_values"][int(next(statuses))]}
            if count == 0:
                hunk.update(head_context="adjacent-context-only", adjacent_line=adjacent)
            hunks.append(hunk)
            base_end, offset = old + removed, offset + delta
        files[member["path"]] = hunks
    return files


def test_manifest_exact_git_hunks_and_direct_locators_including_deletions(changed_repo):
    repository, scope, path = changed_repo
    packet = build(changed_repo)
    manifest = packet["changed_hunks"]
    assert manifest["complete"] is True
    files = manifest_files(packet)
    assert list(files) == sorted(row["path"] for row in packet["inventory"])
    header = re.compile(r"(?m)^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    for member, hunks in files.items():
        patch = git(repository, "diff", "--no-ext-diff", "--no-textconv", "--unified=0",
                    scope["effective_base"], scope["task"]["head"], "--", ":(literal)" + member)
        expected = [(int(a), int(b or "1"), int(c), int(d or "1"))
                    for a, b, c, d in header.findall(patch.decode())]
        assert [tuple(h["base"] + h["head"]) for h in hunks] == expected
    assert [(h["base"], h["head"]) for h in files[path]] == [
        ([200, 1], [200, 1]), ([400, 300], [400, 300]),
        ([1000, 1], [1000, 1]), ([1200, 3], [1199, 0])]
    for member in ("deleted.py", "empty.py", "leading.py", path):
        deletion = next(h for h in files[member] if h["head"][1] == 0)
        assert deletion["head_context"] == "adjacent-context-only"
    assert files["deleted.py"][0]["adjacent_line"] is None
    assert files["empty.py"][0]["adjacent_line"] is None
    assert files["leading.py"][0]["adjacent_line"] == 1
    assert files[path][-1]["adjacent_line"] == 1199
    assert files["added.py"][0]["preload"] == "full"
    assert files["leading.py"][0]["preload"] == "full"
    assert files["deleted.py"][0]["preload"] == "not-in-preload"
    assert files["binary.bin"] == []
    assert "raw_diff(path)" in manifest["locators"]["raw"]
    assert "gita_review(operation='raw_patch',path=path)" in manifest["locators"]["raw"]
    assert "read_file(path,commit=head" in manifest["locators"]["source"]
    assert context.serialize_context({"changed_hunks": manifest})[1:-1] in context.render_context(packet)


@pytest.mark.parametrize("budget", [8000, 12000, 20000, 100000])
def test_all_omitted_hunks_still_discoverable_and_budgets_deterministic(changed_repo, budget):
    baseline = build(changed_repo, budget)
    indexed = build(changed_repo, budget, True)
    again = build(changed_repo, budget)
    assert baseline["inventory"] == indexed["inventory"]
    assert baseline["raw_patch"] == indexed["raw_patch"]
    assert baseline["changed_hunks"] == indexed["changed_hunks"]
    assert context.serialize_context(baseline) == context.serialize_context(again)
    assert len(context.serialize_context(baseline)) <= budget
    hunks = manifest_files(baseline)[changed_repo[2]]
    assert len(hunks) == 4
    assert baseline["current_source"]["coverage"]["omitted_blocks"] > 0
    assert any(h["preload"] == "not-in-preload" for h in hunks)
    if budget == 20000:
        assert any(b["path"] == changed_repo[2] for b in baseline["current_source"]["blocks"])
        assert all(h["preload"] == "not-in-preload" for h in hunks)


def test_private_related_bodies_do_not_count_as_current_preload(changed_repo, monkeypatch):
    repository, scope, path = changed_repo
    raw = git(repository, "show", scope["task"]["head"] + ":" + path)
    lines = raw.decode().splitlines(keepends=True)
    source = {"path": path, "commit": scope["task"]["head"],
              "span": {"start_line": 400, "end_line": 404},
              "text": "".join(lines[399:404])}
    monkeypatch.setattr(context, "related_sources",
                        lambda *args, **kwargs: {"sources": [source]})
    packet = build(changed_repo, 20000)
    files = manifest_files(packet)
    hunks = files[path]
    assert packet["related"]["sources"] == [source]
    assert hunks[1]["preload"] == "not-in-preload"
    assert "current_source.blocks only" in packet["changed_hunks"]["coverage"]
    assert any(b["path"] == path for b in packet["current_source"]["blocks"])
    assert hunks[2]["preload"] == "not-in-preload"
    manifest = {"files": [{"path": member, "hunks": rows} for member, rows in files.items()]}
    # Coverage of current-source spans must count unions, not same-file membership.
    context._manifest_coverage(manifest, [source, source])
    assert hunks[1]["preload"] == "partial"
    context._manifest_coverage(manifest, [
        source, source, {"path": path, "start_line": 405, "end_line": 699}])
    assert hunks[1]["preload"] == "full"
    assert hunks[0]["preload"] == "not-in-preload"
    assert hunks[2]["preload"] == "not-in-preload"


def test_complete_manifest_refuses_instead_of_silently_losing_hunks(changed_repo):
    with pytest.raises(ProtocolError, match="Complete changed-hunk manifest.*refusing"):
        build(changed_repo, 2000)


def test_compact_ranges_roundtrip_defaults_offsets_and_explicit_adjustments():
    hunks = [
        {"base": [0, 0], "head": [1, 2], "preload": "not-in-preload"},
        {"base": [10, 3], "head": [11, 0], "preload": "not-in-preload",
         "adjacent_line": 11, "head_context": "adjacent-context-only"},
        {"base": [20, 1], "head": [19, 1], "preload": "not-in-preload"},
        {"base": [30, 2], "head": [40, 4], "preload": "not-in-preload"},
    ]
    compact = context._compact_manifest({"files": [{"path": "a.py", "hunks": hunks}]}, ["a.py"])
    assert manifest_files({"changed_hunks": compact, "inventory": [{"path": "a.py"}]}) == {
        "a.py": hunks}
    assert all(len(row) >= 1 for row in compact["hunks"][0])
    assert len(compact["hunks"][0][2]) == 1
