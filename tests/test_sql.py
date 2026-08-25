"""SQL entity extraction.

Every test here is a defect that was found by running the scanner over 459 files
from a real T-SQL project, and most of them are the reason the tree-sitter `sql`
grammar was rejected: it named `CreateApprovalPlan.sql` after `NVARCHAR`.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import diff_revisions
from gita.entities.extractor import extract_path
from gita.entities.sql import definitions, extract_sql
from gita.vcs.git import Repo

PROC = """\
-- header comment
CREATE PROCEDURE [dbo].[CreateDemandRequest]
    @DemandRequests [dbo].[DemandRequestTVP] READONLY,
    @CreatedBy NVARCHAR(255)
AS
BEGIN
    SET NOCOUNT ON;
    CREATE TABLE #Filtered (Id INT);
    RAISERROR('CreatedBy cannot be null', 16, 1);
    SELECT 1;
END;
"""


def names(source: str) -> list[str]:
    return [name for _off, _kw, name, _kind in definitions(source)]


class TestNamesAreNeverInvented:
    """A wrong name is worse than a coarse one.

    The grammar returned parameter types and local variables as object names on
    23% of a real corpus. Nothing here may do that.
    """

    def test_bracketed_schema_qualified_name(self):
        assert names(PROC) == ["dbo.CreateDemandRequest"]

    @pytest.mark.parametrize("header,expected", [
        ("CREATE PROCEDURE dbo.Plain AS BEGIN SELECT 1; END;", "dbo.Plain"),
        ('CREATE PROCEDURE "quoted"."Name" AS BEGIN SELECT 1; END;', "quoted.Name"),
        ("CREATE PROC Short AS BEGIN SELECT 1; END;", "Short"),
        ("CREATE OR ALTER PROCEDURE dbo.Upsert AS BEGIN SELECT 1; END;", "dbo.Upsert"),
        ("CREATE UNIQUE NONCLUSTERED INDEX IX_A ON dbo.T (C);", "IX_A"),
        ("CREATE FULLTEXT CATALOG [SearchCatalog] AS DEFAULT;", "SearchCatalog"),
        ("ALTER TABLE [dbo].[DemandRequest] ADD Col INT;", "dbo.DemandRequest"),
    ])
    def test_dialect_spellings(self, header, expected):
        assert names(header) == [expected]

    def test_a_parameter_type_is_not_a_name(self):
        """`CreateApprovalPlan.sql` came back as `NVARCHAR` from tree-sitter."""
        assert "NVARCHAR" not in names(PROC)

    def test_a_local_variable_is_not_a_name(self):
        """`CreateUserBookmark.sql` came back as `@IsDefault`."""
        assert not any(n.startswith("@") for n in names(PROC))


class TestDefinitionsInsideARoutineAreLocals:
    """`CREATE TABLE #Filtered` at line 113 of a 917-line procedure was being
    reported as a sibling, which cut the procedure's body off at that line --
    the same class of wrong boundary that disqualified the grammar."""

    def test_a_temp_table_is_not_an_entity(self):
        assert names(PROC) == ["dbo.CreateDemandRequest"]

    def test_the_routine_keeps_its_whole_body(self):
        tree = extract_sql(PROC.encode(), "p.sql")
        proc = [e for e in tree.entities.values()
                if e.name == "dbo.CreateDemandRequest"]
        assert proc, "procedure not extracted"
        assert proc[0].end_line >= PROC.count("\n"), "body truncated at the temp table"

    def test_a_table_after_a_batch_separator_is_its_own_entity(self):
        source = (PROC + "\nGO\n\nCREATE TABLE [dbo].[Audit] (Id INT);\n")
        assert names(source) == ["dbo.CreateDemandRequest", "dbo.Audit"]


class TestCreateInsideTextIsNotADefinition:
    def test_inside_a_line_comment(self):
        assert names("-- CREATE PROCEDURE dbo.Ghost\nSELECT 1;") == []

    def test_inside_a_block_comment(self):
        assert names("/* CREATE TABLE dbo.Ghost (Id INT); */\nSELECT 1;") == []

    def test_inside_a_nested_block_comment(self):
        """T-SQL block comments nest, and a naive scan stops at the first `*/`."""
        source = "/* outer /* inner */ CREATE TABLE dbo.Ghost (Id INT); */ SELECT 1;"
        assert names(source) == []

    def test_inside_a_string_literal(self):
        assert names("SELECT 'CREATE PROCEDURE dbo.Ghost';") == []

    def test_an_escaped_quote_does_not_unbalance_the_scan(self):
        source = "SELECT 'it''s fine';\nCREATE TABLE [dbo].[Real] (Id INT);"
        assert names(source) == ["dbo.Real"]


class TestWhatCountsAsAChange:
    def repo(self, tmp_path, before, after):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        (tmp_path / "p.sql").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        (tmp_path / "p.sql").write_text(after)
        git("add", "-A")
        git("commit", "-q", "-m", "second")
        return Repo(tmp_path)

    def changed(self, repo):
        return {c.entity.name
                for c in diff_revisions(repo, "HEAD^", "HEAD").material()}

    def test_a_body_edit_names_the_procedure(self, tmp_path):
        after = PROC.replace("cannot be null", "is required")
        assert self.changed(self.repo(tmp_path, PROC, after)) == {
            "dbo.CreateDemandRequest"}

    def test_the_file_is_not_reported_alongside_the_procedure(self, tmp_path):
        """The file entity hashed the whole file, so one edit was counted twice."""
        after = PROC.replace("cannot be null", "is required")
        assert "<module>" not in self.changed(self.repo(tmp_path, PROC, after))

    def test_a_reworded_comment_is_noise(self, tmp_path):
        after = PROC.replace("-- header comment", "-- rewritten header")
        assert self.changed(self.repo(tmp_path, PROC, after)) == set()

    def test_a_reworded_error_message_is_not_noise(self, tmp_path):
        """Blanking string literals for hashing would have hidden this."""
        after = PROC.replace("CreatedBy cannot be null", "CreatedBy is required")
        assert self.changed(self.repo(tmp_path, PROC, after)) == {
            "dbo.CreateDemandRequest"}

    def test_statements_outside_any_definition_belong_to_the_file(self, tmp_path):
        before = PROC + "\nGO\nGRANT EXECUTE ON dbo.CreateDemandRequest TO reader;\n"
        after = PROC + "\nGO\nGRANT EXECUTE ON dbo.CreateDemandRequest TO writer;\n"
        assert self.changed(self.repo(tmp_path, before, after)) == {"<module>"}


class TestRouting:
    @pytest.mark.parametrize("path", ["a.sql", "a.SQL", "a.ddl", "a.tsql"])
    def test_sql_files_reach_the_scanner(self, path):
        tree = extract_path(PROC.encode(), path)
        assert tree.language == "sql"
        assert any(e.name == "dbo.CreateDemandRequest"
                   for e in tree.entities.values())

    def test_other_files_are_untouched(self):
        assert extract_path(b"def f():\n    return 1\n", "a.py").language == "python"
