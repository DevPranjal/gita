"""Display regions are syntax units, not inferred documentation associations."""

import pytest

from gita import diff_trees, extract
from gita.context.answer import compose, material_patch
from gita.context.navigate import focus
from gita.context.patch import display_change_diffs
from gita.diff.changes import ChangeSet
from gita.diff.differ import reconcile_moves


def sample(old: str, new: str, path="m.ts"):
    before, after = old.encode(), new.encode()

    class Source:
        def blob(self, rev, requested_path):
            return before if rev == "old" else after

    changes = reconcile_moves(diff_trees(extract(before, path), extract(after, path)))
    return Source(), ChangeSet(changes, files_changed=1)


def render(repo, changes, **kwargs):
    return compose(repo, "old", "new", changes, budget=kwargs.pop("budget", 10000),
                   respect_raw_diff=False, check_references=False, **kwargs)


def patch_text(result):
    return result.patch if hasattr(result, "patch") else result


OLD = "/**\n * Check access.\n */\nfunction check() { return true; }\n"
COMMENT = "/**\n * Extract roles.\n */\n"
NEW = COMMENT + "function added() { return []; }\n\n" + OLD


def test_added_comment_is_atomic_and_not_assigned_to_the_new_function():
    repo, changes = sample(OLD, NEW)
    plan = display_change_diffs(repo, "old", "new", list(changes))
    assert "+/**\n+ * Extract roles.\n+ */" in plan.patches["m.ts"]
    assert "added comment" in plan.patches["m.ts"]
    assert "Check access" not in plan.patches["m.ts"]
    assert "Extract roles" not in plan.patches["m.ts::added"]
    assert "Check access" not in plan.patches["m.ts::added"]
    result = render(repo, changes)
    assert not result.truncated
    assert "--- comments ::<module>" in result.text
    assert all(c.id != "m.ts" for c in changes.material())
    tree = extract(NEW.encode(), "m.ts")
    assert tree.regions[0].parent_id == "m.ts"
    assert tree.regions[0].kind == "comment"


def test_file_filter_keeps_comments_but_function_filter_does_not_infer_attachment():
    repo, changes = sample(OLD, NEW)
    assert "Extract roles" in render(repo, focus(changes, "m.ts")).text
    assert "Extract roles" not in render(repo, focus(changes, "added")).text
    assert "Extract roles" not in render(repo, focus(changes, interface_only=True)).text


def test_comment_changes_are_whole_removals_and_additions_not_delimiter_matches():
    repo, changes = sample(OLD, OLD.replace("Check access", "Check permissions").replace("true", "false"))
    plan = display_change_diffs(repo, "old", "new", list(changes))
    patch = plan.patches["m.ts"]
    assert "-/**\n- * Check access.\n- */" in patch
    assert "+/**\n+ * Check permissions.\n+ */" in patch
    assert "removed comment" in patch and "added comment" in patch


COMMENT_SLOT = (
    "describe('read', () => {\n  read();\n});\n\n"
    "// Old write heading\n"
    "describe('write', () => {\n  write();\n});\n"
)


@pytest.mark.parametrize("crlf", [False, True])
def test_comment_replacement_between_matched_neighbors_is_one_atomic_hunk(crlf):
    new = "\n\n" + COMMENT_SLOT.replace("Old write", "New write").replace("write();", "save();")
    if crlf:
        new = new.replace("\n", "\r\n")
    repo, changes = sample(COMMENT_SLOT, new)
    plan = display_change_diffs(repo, "old", "new", list(changes))
    assert plan.patches["m.ts"] == (
        "@@ -5,1 +7,1 @@ changed comment\n"
        "-// Old write heading\n+// New write heading\n"
    )
def test_paired_multiline_comments_keep_the_entire_comment_on_both_sides():
    old = COMMENT_SLOT.replace("// Old write heading", "/**\n * Old write heading\n */")
    new = old.replace("Old write", "New write").replace("write();", "save();")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert patch == (
        "@@ -5,3 +5,3 @@ changed comment\n"
        "-/**\n- * Old write heading\n- */\n"
        "+/**\n+ * New write heading\n+ */\n"
    )


@pytest.mark.parametrize("intervening", [
    "const setting = 1;\n",
    "function added() { return 'new'; }\n",
])
def test_new_syntax_breaks_the_comment_slot(intervening):
    new = COMMENT_SLOT.replace("// Old write heading", intervening + "// New write heading")
    new = new.replace("write();", "save();")
    repo, changes = sample(COMMENT_SLOT, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert "changed comment" not in patch
    assert "removed comment" in patch and "added comment" in patch


def test_pairing_does_not_promote_comment_only_edits_to_material_changes():
    repo, changes = sample(COMMENT_SLOT, COMMENT_SLOT.replace("Old write", "New write"))
    assert changes.material() == []
    assert display_change_diffs(repo, "old", "new", list(changes)).patches == {}


def test_changed_comment_relocated_to_another_slot_is_not_paired():
    old = COMMENT_SLOT + "function tail() { return 0; }\n"
    new = old.replace("// Old write heading\n", "").replace("write();", "save();")
    new = new.replace("function tail()", "// New write heading\nfunction tail()")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert "changed comment" not in patch
    assert "removed comment" in patch and "added comment" in patch


def test_multiple_unmatched_comments_are_not_paired_by_position():
    old = COMMENT_SLOT.replace("// Old write heading", "// Old heading\n// Old detail")
    new = old.replace("Old", "New").replace("write();", "save();")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert "changed comment" not in patch
    assert patch.count("removed comment") == patch.count("added comment") == 2


def test_matching_entities_in_different_syntax_containers_do_not_anchor_a_comment():
    old = (
        "function outer() {\n  if (ready) {\n"
        "    function left() { return 1; }\n"
        "    // Old heading\n"
        "    function right() { return 1; }\n"
        "  }\n}\n"
    )
    new = old.replace("if (ready)", "while (ready)").replace("Old", "New")
    new = new.replace("right() { return 1; }", "right() { return 2; }")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts::outer"]
    assert "changed comment" not in patch
    assert "removed comment" in patch and "added comment" in patch


def test_parse_errors_do_not_produce_changed_comment_inferences():
    new = COMMENT_SLOT.replace("Old write", "New write").replace("write();", "write(;")
    assert extract(new.encode(), "m.ts").parse_error
    repo, changes = sample(COMMENT_SLOT, new)
    assert "changed comment" not in render(repo, changes).text


def test_duplicate_comments_do_not_acquire_guessed_identities():
    repeated = "/** same */\n"
    old = repeated + "function a() {}\n" + repeated + "function b() {}\n"
    new = repeated + "function added() {}\n" + old
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert patch.count("-/** same */") == 2
    assert patch.count("+/** same */") == 3


def test_file_header_is_not_attached_to_a_declaration():
    license = "/* License remains unchanged. */\n\n"
    repo, changes = sample(license + OLD, license + NEW)
    plan = display_change_diffs(repo, "old", "new", list(changes))
    assert not any("License" in patch for patch in plan.patches.values())
    assert "Extract roles" in plan.patches["m.ts"]
    assert "Extract roles" not in plan.patches["m.ts::added"]


def test_shared_line_comment_keeps_the_code_line_intact():
    old = "function a() {\n  return 1; // old\n}\n"
    new = old.replace("return 1; // old", "return 2; // new")
    repo, changes = sample(old, new)
    text = render(repo, changes).text
    assert "-  return 1; // old" in text
    assert "+  return 2; // new" in text
    assert "added comment" not in text


def test_patch_mode_uses_the_same_atomic_comment_blocks():
    repo, changes = sample(OLD, NEW)
    patch = patch_text(material_patch(
        repo, "old", "new", changes, budget=10000, respect_raw_diff=False))
    assert "+/**\n+ * Extract roles.\n+ */" in patch
    assert "Check access" not in patch


def test_omitted_comment_context_is_reported_as_incomplete():
    long_comment = "/**\n" + " * Explanation.\n" * 300 + " */\n"
    repo, changes = sample(OLD, long_comment + "function added() { return []; }\n" + OLD)
    result = render(repo, changes, budget=300)
    assert result.truncated
    assert "code not shown" in result.text
    assert result.tokens <= 300
    assert "function added()" in result.text


OLD_STATEMENT = (
    "  void load()\n"
    "    .then(value => {\n"
    "      consume(value);\n"
    "    })\n"
    "    .catch(() => undefined);\n"
)
NEW_STATEMENT = (
    "  void (async () => {\n"
    "    const value = await load();\n"
    "    consume(value);\n"
    "  })().catch(() => undefined);\n"
)


def handler(statement, import_name="oldName"):
    return f"import {{ {import_name} }} from 'dep';\nfunction handler() {{\n{statement}}}\n"


def test_cross_owner_statement_is_one_complete_replacement():
    repo, changes = sample(handler(OLD_STATEMENT), handler(NEW_STATEMENT, "newName"))
    plan = display_change_diffs(repo, "old", "new", list(changes))
    patch = plan.patches["m.ts::handler"]
    assert "@@ -3,5 +3,4 @@ statement replaced" in patch
    assert sum(line.startswith("@@") for line in patch.splitlines()) == 1
    for line in OLD_STATEMENT.splitlines():
        assert "-" + line in patch
    for line in NEW_STATEMENT.splitlines():
        assert "+" + line in patch
    assert "import" not in patch
    assert "void" not in plan.patches["m.ts"]
    children = [c.id for c in changes.material() if c.id.startswith("m.ts::handler::")]
    assert children
    assert all(not plan.patches[c] for c in children)
    assert all(c in plan.covered({"m.ts::handler"}) for c in children)
    result = render(repo, changes)
    assert result.detailed == ["m.ts::handler", "m.ts"]
    assert not result.truncated


def test_unchanged_neighboring_statements_are_not_swept_into_replacement():
    old = handler("  warmup();\n" + OLD_STATEMENT + "  finish();\n")
    new = handler("  warmup();\n" + NEW_STATEMENT + "  finish();\n")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts::handler"]
    assert "statement replaced" in patch
    assert "warmup" not in patch
    assert "finish" not in patch


def test_one_statement_can_change_from_parent_owned_to_callback_owned():
    repo, changes = sample(handler("  void load();\n"), handler(NEW_STATEMENT))
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts::handler"]
    assert "@@ -3,1 +3,4 @@ statement replaced" in patch
    assert "-  void load();" in patch
    assert "+  void (async () => {" in patch


def test_import_change_does_not_promote_an_entire_test_suite():
    old = ("import { oldName } from 'dep';\n"
           "describe('suite', () => {\n"
           "  it('a', () => one());\n"
           "  it('b', () => two());\n"
           "});\n")
    new = old.replace("oldName", "newName").replace("one()", "three()").replace("two()", "four()")
    repo, changes = sample(old, new)
    result = render(repo, changes)
    assert "statement replaced" not in result.text
    assert "three()" not in display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]


def test_sibling_parent_edit_does_not_promote_an_unrelated_callback_statement():
    old = handler("  configure(1);\n  run(() => {\n    oldBehavior();\n  });\n")
    new = old.replace("configure(1)", "configure(2)").replace("oldBehavior", "newBehavior")
    repo, changes = sample(old, new)
    plan = display_change_diffs(repo, "old", "new", list(changes))
    assert "statement replaced" not in render(repo, changes).text
    assert "oldBehavior" not in plan.patches["m.ts::handler"]
    assert "newBehavior" not in plan.patches["m.ts::handler"]


def test_ambiguous_statement_pairs_remain_separate_additions_and_removals():
    old = handler(OLD_STATEMENT + OLD_STATEMENT.replace("load()", "other()"))
    new = handler(NEW_STATEMENT + NEW_STATEMENT.replace("load()", "other()"))
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts::handler"]
    assert "statement replaced" not in patch
    assert patch.count("removed statement") == 2
    assert patch.count("added statement") == 2


def test_statements_in_different_control_flow_containers_are_not_paired():
    old = handler("  if (ready) {\n" + OLD_STATEMENT + "  }\n")
    new = handler(NEW_STATEMENT)
    repo, changes = sample(old, new)
    result = render(repo, changes)
    assert "statement replaced" not in result.text
    assert "-  void load()" in result.text
    assert "+  void (async () => {" in result.text


def test_parse_errors_do_not_produce_guessed_statement_replacements():
    old = handler(OLD_STATEMENT)[:-2]
    new = handler(NEW_STATEMENT)
    assert extract(old.encode(), "m.ts").parse_error
    repo, changes = sample(old, new)
    assert "statement replaced" not in render(repo, changes).text


def test_comments_inside_a_grouped_statement_are_not_printed_twice():
    old = handler(OLD_STATEMENT.replace("      consume", "      // old comment\n      consume"))
    new = handler(NEW_STATEMENT.replace("    consume", "    // new comment\n    consume"))
    repo, changes = sample(old, new)
    result = render(repo, changes)
    assert result.text.count("-      // old comment") == 1
    assert result.text.count("+    // new comment") == 1
    assert "statement replaced" in result.text
    assert "added comment" not in result.text
    assert "removed comment" not in result.text


def test_narrow_callback_filter_does_not_acquire_its_parent_statement():
    repo, changes = sample(handler(OLD_STATEMENT), handler(NEW_STATEMENT))
    result = render(repo, focus(changes, "load"))
    assert "statement replaced" not in result.text
    assert "void load()" not in result.text
    assert "async" not in result.text
    assert "consume(value)" in result.text


def test_grouped_code_withheld_by_budget_is_not_reported_as_covered():
    large = NEW_STATEMENT.replace("    consume(value);", "    consume(value);\n" * 200)
    repo, changes = sample(handler(OLD_STATEMENT), handler(large, "newName"))
    plan = display_change_diffs(repo, "old", "new", list(changes))
    required_children = [c.id for c in changes.material()
                         if c.id.startswith("m.ts::handler::") and plan.required[c.id]]
    assert required_children
    assert all("m.ts::handler" in plan.required[c] for c in required_children)
    result = render(repo, changes, budget=300)
    assert "m.ts::handler" not in result.detailed
    assert result.truncated
    assert "code not shown" in result.text
    assert result.tokens <= 300


def test_unchanged_blank_gap_is_not_relocated_to_an_unaffected_function():
    old = ("export function first() { return 1; }\n\n"
           "/** Existing. */\nexport function next() { return 2; }\n\n"
           "/** Tail. */\nexport function last() { return 3; }\n")
    new = old.replace(
        "export function first() { return 1; }",
        "export function first() { return added(); }\n\n"
        "/** New. */\nexport function added() { return 1; }",
    )
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    unchanged_blank = new.splitlines().index("/** Tail. */")
    assert f"+{unchanged_blank},1 @@" not in patch
    assert "+/** New. */" in patch


def test_changed_comment_spacing_is_not_discarded_as_an_unchanged_gap():
    old = ("import 'old';\nfunction keep() {}\n"
           "/** Directive. */\nfunction tail() {}\n")
    new = old.replace("'old'", "'new'").replace("/** Directive.", "\n/** Directive.")
    repo, changes = sample(old, new)
    patch = display_change_diffs(repo, "old", "new", list(changes)).patches["m.ts"]
    assert "@@ -1,0 +3,1 @@\n+\n" in patch
