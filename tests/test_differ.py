from gita import ChangeKind, diff_trees, extract, similarity
from gita.diff.differ import diff_files


def changes_by_id(previous: bytes, current: bytes, path: str = "m.py"):
    old = extract(previous, path) if previous is not None else None
    new = extract(current, path) if current is not None else None
    return {c.id: c for c in diff_trees(old, new)}


class TestBasicClassification:
    def test_added(self):
        result = changes_by_id(b"def a():\n    return 1\n",
                               b"def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        assert result["m.py::b"].kind is ChangeKind.ADDED
        assert result["m.py::a"].kind is ChangeKind.UNCHANGED

    def test_removed(self):
        result = changes_by_id(b"def a():\n    return 1\n\n\ndef b():\n    return 2\n",
                               b"def a():\n    return 1\n")
        assert result["m.py::b"].kind is ChangeKind.REMOVED

    def test_body_changed(self):
        result = changes_by_id(b"def a(x):\n    return x + 1\n",
                               b"def a(x):\n    return x + 2\n")
        change = result["m.py::a"]
        assert change.kind is ChangeKind.BODY_CHANGED
        assert change.body_changed and not change.signature_changed
        assert not change.affects_interface

    def test_signature_changed(self):
        result = changes_by_id(b"def a(x):\n    return x\n",
                               b"def a(x, y=0):\n    return x\n")
        change = result["m.py::a"]
        assert change.kind is ChangeKind.SIGNATURE_CHANGED
        assert change.affects_interface

    def test_whole_file_added(self):
        result = changes_by_id(None, b"def a():\n    return 1\n")
        assert result["m.py::a"].kind is ChangeKind.ADDED

    def test_whole_file_removed(self):
        result = changes_by_id(b"def a():\n    return 1\n", None)
        assert result["m.py::a"].kind is ChangeKind.REMOVED


class TestNoiseFiltering:
    def test_reformatting_is_cosmetic(self):
        result = changes_by_id(b"def a(x,y):\n    return x+y\n",
                               b"def a(x, y):\n    return x + y\n")
        change = result["m.py::a"]
        assert change.kind is ChangeKind.COSMETIC
        assert change.is_noise

    def test_comment_edit_is_cosmetic(self):
        result = changes_by_id(b"def a():\n    # before\n    return 1\n",
                               b"def a():\n    # after, much longer\n    return 1\n")
        assert result["m.py::a"].kind is ChangeKind.COSMETIC

    def test_identical_source_is_unchanged(self):
        source = b"def a():\n    return 1\n"
        assert changes_by_id(source, source)["m.py::a"].kind is ChangeKind.UNCHANGED

    def test_noise_is_excluded_from_material(self):
        old = extract(b"def a(x,y):\n    return x+y\n", "m.py")
        new = extract(b"def a(x, y):\n    return x + y\n", "m.py")
        from gita import ChangeSet

        changeset = ChangeSet()
        changeset.extend(diff_trees(old, new))
        assert len(changeset) == 1
        assert changeset.material() == []


class TestMoveAndRename:
    def test_move_between_containers_keeps_name(self):
        previous = b"class A:\n    def shared(self):\n        return 42\n\n\nclass B:\n    pass\n"
        current = b"class A:\n    pass\n\n\nclass B:\n    def shared(self):\n        return 42\n"
        moved = [c for c in diff_trees(extract(previous, "m.py"), extract(current, "m.py"))
                 if c.kind is ChangeKind.MOVED]
        assert len(moved) == 1
        assert moved[0].previous.id == "m.py::A::shared"
        assert moved[0].current.id == "m.py::B::shared"

    def test_identical_body_under_new_name_is_rename(self):
        result = changes_by_id(b"def old_name(x):\n    return x * 2\n",
                               b"def new_name(x):\n    return x * 2\n")
        change = result["m.py::new_name"]
        assert change.kind is ChangeKind.RENAMED
        assert change.previous.name == "old_name"
        assert change.similarity == 1.0

    def test_rename_with_edits_detected_by_similarity(self):
        previous = (b"def compute_total(items):\n"
                    b"    total = 0\n"
                    b"    for item in items:\n"
                    b"        total += item.price\n"
                    b"    return total\n")
        current = (b"def calculate_total(items):\n"
                   b"    total = 0\n"
                   b"    for item in items:\n"
                   b"        total += item.price\n"
                   b"    return round(total)\n")
        result = changes_by_id(previous, current)
        change = result["m.py::calculate_total"]
        assert change.kind is ChangeKind.RENAMED
        assert change.previous.name == "compute_total"
        assert 0.6 <= change.similarity < 1.0

    def test_unrelated_functions_are_not_renames(self):
        previous = (b"def parse_config(path):\n"
                    b"    with open(path) as handle:\n"
                    b"        return json.load(handle)\n")
        current = (b"def send_email(recipient, subject):\n"
                   b"    server.connect()\n"
                   b"    server.send(recipient, subject)\n")
        result = changes_by_id(previous, current)
        assert result["m.py::send_email"].kind is ChangeKind.ADDED
        assert result["m.py::parse_config"].kind is ChangeKind.REMOVED


class TestCrossFileMoves:
    """diff_trees sees one file at a time; reconcile_moves closes the gap."""

    HELPER = (b"def shared(values):\n"
              b"    total = 0\n"
              b"    for value in values:\n"
              b"        total += value\n"
              b"    return total\n")

    def build(self, pairs):
        return diff_files([
            (extract(old, path) if old is not None else None,
             extract(new, path) if new is not None else None)
            for path, old, new in pairs
        ])

    def test_function_moved_to_another_file(self):
        changeset = self.build([
            ("a.py", self.HELPER, b""),
            ("b.py", b"def other():\n    return 1\n",
             b"def other():\n    return 1\n\n\n" + self.HELPER),
        ])
        moved = changeset.by_kind(ChangeKind.MOVED)
        assert len(moved) == 1
        assert moved[0].previous.path == "a.py"
        assert moved[0].current.path == "b.py"
        assert changeset.by_kind(ChangeKind.ADDED) == []
        assert changeset.by_kind(ChangeKind.REMOVED) == []

    def test_function_moved_and_renamed_across_files(self):
        renamed = self.HELPER.replace(b"def shared(", b"def total_of(")
        changeset = self.build([
            ("a.py", self.HELPER, b""),
            ("b.py", b"", renamed),
        ])
        matches = changeset.by_kind(ChangeKind.RENAMED)
        assert len(matches) == 1
        assert matches[0].previous.name == "shared"
        assert matches[0].current.name == "total_of"

    def test_unrelated_add_and_delete_stay_separate(self):
        changeset = self.build([
            ("a.py", b"def parse(path):\n    return json.load(open(path))\n", b""),
            ("b.py", b"", b"def send(to, subject):\n    smtp.deliver(to, subject)\n"),
        ])
        assert len(changeset.by_kind(ChangeKind.ADDED)) == 1
        assert len(changeset.by_kind(ChangeKind.REMOVED)) == 1
        assert changeset.by_kind(ChangeKind.MOVED) == []

    def test_ambiguous_duplicates_are_not_matched(self):
        # two classes carrying an identical `helper`: the method bodies AND names
        # collide, so the hash is ambiguous on both sides
        source = (b"class X:\n"
                  b"    def helper(self, v):\n"
                  b"        total = 0\n"
                  b"        for i in v:\n"
                  b"            total += i\n"
                  b"        return total\n"
                  b"\n\n"
                  b"class Y:\n"
                  b"    def helper(self, v):\n"
                  b"        total = 0\n"
                  b"        for i in v:\n"
                  b"            total += i\n"
                  b"        return total\n")
        changeset = self.build([("a.py", source, b""), ("b.py", b"", source)])

        moved = {c.current.qualname for c in changeset.by_kind(ChangeKind.MOVED)}
        # the distinct classes are unambiguous and do match
        assert moved == {"X", "Y"}
        # the identical methods are not: guessing between them would be a coin flip
        unmatched = {c.entity.qualname for c in changeset.by_kind(ChangeKind.ADDED)}
        assert unmatched == {"X::helper", "Y::helper"}


class TestSimilarity:
    def test_identical_bodies_score_one(self):
        source = b"def f():\n    return 1\n"
        a = extract(source, "m.py").get("m.py::f")
        assert similarity(a, a) == 1.0

    def test_disjoint_bodies_score_low(self):
        a = extract(b"def f():\n    return alpha + beta\n", "m.py").get("m.py::f")
        b = extract(b"def g():\n    launch(rocket, payload)\n", "m.py").get("m.py::g")
        assert similarity(a, b) < 0.3


class TestJavaScript:
    def test_callback_body_change_is_attributed_to_its_path(self):
        previous = b"describe('R', () => {\n  it('works', () => {\n    expect(1).toBe(1);\n  });\n});\n"
        current = b"describe('R', () => {\n  it('works', () => {\n    expect(2).toBe(2);\n  });\n});\n"
        result = {c.id: c for c in diff_trees(extract(previous, "t.js"), extract(current, "t.js"))}
        target = "t.js::describe('R')::it('works')"
        assert result[target].kind is ChangeKind.BODY_CHANGED
