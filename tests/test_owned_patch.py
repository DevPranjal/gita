"""Owned hunks must retain real coordinates across gaps in source ownership."""

import random
import re

import pytest

from gita import ChangeKind, diff_trees, extract
from gita.context.patch import _owned_patch, display_change_diffs
from gita.diff.changes import EntityChange


class Source:
    def __init__(self, blobs):
        self.blobs = blobs

    def blob(self, rev, path):
        return self.blobs.get((rev, path))


def test_discontinuous_owned_hunks_preserve_every_edit():
    rng = random.Random(17)
    for _ in range(100):
        old = [(i * 3, f"value_{i}\n") for i in range(rng.randrange(1, 20))]
        values = [text for _, text in old]
        for _ in range(5):
            i = rng.randrange(len(values) + 1)
            values[i:i + rng.randrange(2)] = [f"changed_{rng.randrange(20)}\n"]
        new = [(i * 4 + 2, text) for i, text in enumerate(values)]
        sources = [dict(old), dict(new)]
        edits = [set(), set()]
        expected = consumed = None
        for line in _owned_patch(old, new, 1).splitlines():
            if line.startswith("@@"):
                if expected is not None:
                    assert consumed == expected
                match = re.fullmatch(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@", line)
                start_old, count_old, start_new, count_new = map(int, match.groups())
                positions = [start_old - bool(count_old), start_new - bool(count_new)]
                expected, consumed = [count_old, count_new], [0, 0]
            else:
                for side, sign in enumerate(("-", "+")):
                    if line[0] in (" ", sign):
                        number = positions[side]
                        assert sources[side][number].rstrip("\n") == line[1:]
                        if line[0] == sign:
                            assert number not in edits[side]
                            edits[side].add(number)
                        positions[side] += 1
                        consumed[side] += 1
        assert consumed == expected
        assert ([text for number, text in old if number not in edits[0]]
                == [text for number, text in new if number not in edits[1]])


def test_shared_line_uses_utf8_byte_columns():
    old = "const label = '\U0001f600'; const cb = useCallback(() => 1, [oldDep]);\r\n".encode()
    new = old.replace(b"oldDep", b"newDep")
    changes = diff_trees(extract(old, "m.ts"), extract(new, "m.ts"))

    source = Source({("old", "m.ts"): old, ("new", "m.ts"): new})
    patches = display_change_diffs(
        source, "old", "new", [c for c in changes if not c.is_noise]).patches
    assert "oldDep" in patches["m.ts"]
    assert "newDep" in patches["m.ts"]


def test_filter_does_not_hide_selected_edits_on_shared_lines():
    cases = [
        (b"const first = () => 1; const second = () => 2;\n",
         b"const first = () => 11; const second = () => 22;\n", "m.ts::first", "11"),
        (b"const cb = useCallback(() => oldBody(), [oldDep]);\n",
         b"const cb = useCallback(() => newBody(), [newDep]);\n", "m.ts", "newDep"),
    ]
    for old, new, entity_id, expected in cases:
        changes = diff_trees(extract(old, "m.ts"), extract(new, "m.ts"))

        selected = [c for c in changes if c.id == entity_id and not c.is_noise]
        source = Source({("old", "m.ts"): old, ("new", "m.ts"): new})
        patches = display_change_diffs(source, "old", "new", selected).patches
        assert expected in patches[entity_id]


def test_cross_file_match_retains_both_owned_bodies():
    old = b"import os\n\ndef moved():\n    return 1\n"
    new = b"def moved():\n    return 2\n"
    change = EntityChange(
        ChangeKind.MOVED,
        current=extract(new, "new.py").get("new.py::moved"),
        previous=extract(old, "old.py").get("old.py::moved"),
    )

    source = Source({("old", "old.py"): old, ("new", "new.py"): new})
    patch = display_change_diffs(source, "old", "new", [change]).patches[change.id]
    assert "-    return 1" in patch
    assert "+    return 2" in patch
    assert "import os" not in patch


def parsed_patches(old, new, path="m.ts"):
    changes = diff_trees(extract(old, path), extract(new, path))

    material = [c for c in changes if not c.is_noise]
    source = Source({("old", path): old, ("new", path): new})
    return source, changes, display_change_diffs(source, "old", "new", material).patches


def test_separator_only_parent_hunks_disappear_without_a_missing_code_notice():
    from gita.context.answer import compose, material_patch
    from gita.diff.changes import ChangeSet

    old = b"describe('suite', () => {\n  it('a', () => expect(1));\n});\n"
    new = old.replace(b"});\n", b"\n  it('b', () => expect(2));\n\n});\n")
    repo, changes, patches = parsed_patches(old, new)
    parent = "m.ts::describe('suite')"
    assert patches[parent] == ""
    assert "+  it('b', () => expect(2));" in patches[parent + "::it('b')"]
    result = compose(repo, "old", "new", ChangeSet(changes, files_changed=1),
                     budget=10000, respect_raw_diff=False, check_references=False)
    assert parent not in result.detailed
    assert not result.truncated
    assert "code not shown" not in result.text
    assert "body_changed describe('suite')" in result.text
    patch = material_patch(repo, "old", "new", ChangeSet(changes, files_changed=1),
                           budget=10000, respect_raw_diff=False).patch
    assert "body_changed" not in patch
    assert "+  it('b', () => expect(2));" in patch


@pytest.mark.parametrize("removed", [False, True])
def test_only_parser_confirmed_blank_hunks_are_suppressed(removed):
    blank = [(3, " \t\n")]
    old, new = (blank, []) if removed else ([], blank)
    assert _owned_patch(old, new, 0)
    assert _owned_patch(old, new, 0, old_trivia=frozenset({4}),
                        new_trivia=frozenset({4})) == ""


def test_mixed_hunk_retains_its_blank_line_and_real_edit():
    patch = _owned_patch([], [(0, "\n"), (1, "run();\n")], 0,
                         new_trivia=frozenset({1}))
    assert "@@ -0,0 +1,2 @@" in patch
    assert "+\n+run();" in patch


@pytest.mark.parametrize("old,new", [
    ("  run();\n", "    run();\n"),
    ("}\n", ");\n"),
])
def test_nonblank_edits_are_never_filtered_as_blank_trivia(old, new):
    patch = _owned_patch([(0, old)], [(0, new)], 0,
                         old_trivia=frozenset({1}), new_trivia=frozenset({1}))
    assert "-" + old.rstrip("\n") in patch
    assert "+" + new.rstrip("\n") in patch


@pytest.mark.parametrize("path,source", [
    ("m.ts", b"const value = `first\nlast`;\n"),
    ("m.ts", b"const value = `first\n${value}\nlast`;\n"),
    ("m.py", b"def value():\n    return '''first\nlast'''\n"),
    ("m.go", b"package p\nvar value = `first\nlast`\n"),
    ("m.rs", b'const VALUE: &str = r#"first\nlast"#;\n'),
    ("m.tsx", b"const value = <pre>first\nlast</pre>;\n"),
])
def test_hunk_filter_preserves_blank_literal_content(path, source):
    new = source.replace(b"first\n", b"first\n\n")
    patch = _owned_patch(
        list(enumerate(source.decode().splitlines(keepends=True))),
        list(enumerate(new.decode().splitlines(keepends=True))), 0,
        old_trivia=extract(source, path).layout_lines,
        new_trivia=extract(new, path).layout_lines,
    )
    assert "\n+\n" in patch


def test_literal_blank_hunk_survives_beside_a_material_parent_edit():
    old = (b"const marker = 1;\n"
           b"const a = 0;\nconst b = 0;\nconst c = 0;\n"
           b"const value = `first\n${value}\nlast`;\n")
    new = old.replace(b"marker = 1", b"marker = 2").replace(b"first\n", b"first\n\n")
    _, _, patches = parsed_patches(old, new)
    assert "+const marker = 2;" in patches["m.ts"]
    assert "\n+\n" in patches["m.ts"]


def test_parse_error_on_either_side_disables_hunk_suppression(monkeypatch):
    import gita.context.patch as module

    captured = []
    original = module._owned_patch

    def record(*args, **kwargs):
        captured.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_owned_patch", record)
    old = b"function broken( {\n"
    new = b"function fixed() {\n\n  return 1;\n}\n"
    parsed_patches(old, new)
    assert captured
    assert all(not call["old_trivia"] and not call["new_trivia"] for call in captured)


@pytest.mark.parametrize("path,source", [
    ("m.md", b"first\n\nlast\n"),
    ("m.yaml", b"value: |\n  first\n\n  last\n"),
    ("m.ts", b"function broken( {\n\n"),
    ("m.py", b"\n# coding: latin-1\nprint(1)\n"),
    ("m.ts", b"/* first\n\nlast */\nconst value = 1;\n"),
    ("m.go", b"//go:build linux\n\npackage p\n"),
])
def test_uncertain_or_comment_related_blank_lines_are_not_layout_trivia(path, source):
    assert not extract(source, path).layout_lines
