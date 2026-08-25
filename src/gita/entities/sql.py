"""SQL entity extraction without a grammar.

The `sql` grammar in the language pack is written for PostgreSQL. Measured over
459 T-SQL files from a real database project it parsed 98% of them with errors
and, worse, produced *confident wrong names*: `CreateApprovalPlan.sql` came back
as `NVARCHAR` and `CreateUserBookmark.sql` as `@IsDefault`, because the bracketed
identifier `[dbo].[Name]` landed inside an ERROR node and the next
`object_reference` was a parameter type. 23% of files were named after something
that was not the object they define.

A wrong name is worse than no name. gita's guarantee is that it degrades to
coarser answers, never to false ones, so the grammar is not usable here.

What SQL offers instead is an unusually regular shape: objects are introduced by
`CREATE`/`ALTER` at the start of a statement, and the name follows the object
keyword. That is worth scanning for directly. The scanner below is deliberately
shallow -- it finds top-level definitions and nothing inside them -- because the
useful unit of a SQL change is "which procedure changed", and anything finer
would mean re-implementing a parser badly.

Dialects differ in what they quote with (`[x]`, `"x"`, `` `x` ``) and how they
separate batches (`GO`), so all of those are handled; they do not differ in the
`CREATE <kind> <name>` shape, which is what this relies on.
"""

from __future__ import annotations

import re

from .model import Entity, EntityKind, EntityTree, digest

#: Handled here rather than through a LanguageSpec, because there is no grammar
#: behind it. `.ddl` and `.tsql` are the same language under other names.
SQL_EXTENSIONS = (".sql", ".ddl", ".tsql")

#: Object kinds worth reporting as entities, mapped onto gita's kind vocabulary.
#: `PROC` is the T-SQL abbreviation of `PROCEDURE`.
OBJECT_KINDS: dict[str, EntityKind] = {
    "PROCEDURE": EntityKind.FUNCTION,
    "PROC": EntityKind.FUNCTION,
    "FUNCTION": EntityKind.FUNCTION,
    "TRIGGER": EntityKind.FUNCTION,
    "VIEW": EntityKind.TABLE,
    "TABLE": EntityKind.TABLE,
    "INDEX": EntityKind.CONSTANT,
    "SEQUENCE": EntityKind.CONSTANT,
    "SYNONYM": EntityKind.CONSTANT,
    "TYPE": EntityKind.TYPE,
    "SCHEMA": EntityKind.MODULE,
    # Security and catalog objects. `CATALOG` is reached via the `FULLTEXT`
    # modifier, so `CREATE FULLTEXT CATALOG x` needs no special case.
    "USER": EntityKind.CONSTANT,
    "ROLE": EntityKind.CONSTANT,
    "LOGIN": EntityKind.CONSTANT,
    "CATALOG": EntityKind.CONSTANT,
}

#: Modifiers that may sit between CREATE and the object keyword.
_MODIFIERS = frozenset({
    "OR", "ALTER", "REPLACE", "UNIQUE", "CLUSTERED", "NONCLUSTERED",
    "COLUMNSTORE", "MATERIALIZED", "TEMPORARY", "TEMP", "GLOBAL", "LOCAL",
    "FULLTEXT",
})

_INTRO = re.compile(r"\b(CREATE|ALTER)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
#: One segment of a possibly-qualified name: [bracketed], "quoted", `backtick`
#: or bare. Schema qualification is joined back together by the caller.
_SEGMENT = re.compile(r'\s*(?:\[([^\]]+)\]|"([^"]+)"|`([^`]+)`|([A-Za-z_@#][\w@#$]*))')
#: A batch separator, which is the only reliable end of a routine body.
_BATCH = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)

#: Kinds whose body contains arbitrary statements, including other definitions.
#: A `CREATE TABLE #Filtered` inside a procedure is a local, not a schema object.
_ROUTINES = frozenset({"PROCEDURE", "PROC", "FUNCTION", "TRIGGER"})


def _blank_noise(source: str, strings: bool = True) -> str:
    """Replace comments -- and optionally string literals -- with spaces.

    Offsets have to survive so entity boundaries stay true to the original text;
    only the *content* is neutralised, so a `CREATE` inside a comment or a quoted
    string cannot be mistaken for a definition.

    Hashing masks comments but keeps strings: a reworded `RAISERROR` message is a
    real change, and blanking literals would make it invisible.
    """
    out = list(source)
    i, n = 0, len(source)
    while i < n:
        two = source[i:i + 2]
        if two == "--":
            j = source.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif two == "/*":
            # T-SQL block comments nest, and a naive scan would stop early.
            depth, j = 1, i + 2
            while j < n and depth:
                if source[j:j + 2] == "/*":
                    depth += 1
                    j += 2
                elif source[j:j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            for k in range(i, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif source[i] == "'":
            if not strings:
                i += 1
                continue
            j = i + 1
            while j < n:
                if source[j] == "'":
                    if source[j + 1:j + 2] == "'":   # '' is an escaped quote
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _read_name(masked: str, source: str, pos: int) -> tuple[str, int] | None:
    """Read a possibly schema-qualified name starting at ``pos``."""
    parts: list[str] = []
    at = pos
    while True:
        match = _SEGMENT.match(masked, at)
        if not match:
            break
        # Bracketed and quoted segments are masked-safe; read them from the
        # original so the reported name is exactly what the file says.
        raw = source[match.start(): match.end()].strip()
        parts.append(raw.strip('[]"`'))
        at = match.end()
        if masked[at:at + 1] == ".":
            at += 1
            continue
        break
    if not parts:
        return None
    return ".".join(parts), at


def definitions(source: str) -> list[tuple[int, str, str, EntityKind]]:
    """Every *schema-level* object definition, as (offset, keyword, name, kind).

    Definitions inside a routine body are locals and are left out: a procedure
    that declares `#Filtered` halfway through defines one object, not two, and
    treating the temp table as a sibling would silently cut the procedure's body
    off at that line.
    """
    masked = _blank_noise(source)
    batches = [m.start() for m in _BATCH.finditer(masked)]

    def next_batch(after: int) -> int:
        for start in batches:
            if start > after:
                return start
        return len(source)

    found: list[tuple[int, str, str, EntityKind]] = []
    body_ends_at = -1
    for intro in _INTRO.finditer(masked):
        if intro.start() < body_ends_at:
            continue
        at = intro.end()
        keyword = None
        # Skip modifiers such as `OR ALTER` or `UNIQUE NONCLUSTERED`.
        for _ in range(6):
            word = _WORD.search(masked, at)
            if not word or not masked[at:word.start()].strip() == "":
                break
            token = word.group(0).upper()
            if token in OBJECT_KINDS:
                keyword, at = token, word.end()
                break
            if token not in _MODIFIERS:
                break
            at = word.end()
        if keyword is None:
            continue
        read = _read_name(masked, source, at)
        if not read:
            continue
        name, _ = read
        # `#temp`, `##global` and `@table` are locals of the enclosing batch.
        if name.lstrip("[").startswith(("#", "@")):
            continue
        found.append((intro.start(), keyword, name, OBJECT_KINDS[keyword]))
        if keyword in _ROUTINES:
            body_ends_at = next_batch(intro.start())
    return found


def extract_sql(source: bytes, path: str) -> EntityTree:
    """A SQL file as its top-level objects.

    Each object runs from its own `CREATE`/`ALTER` to the start of the next one,
    which is where a batch separator would sit anyway. Statements outside any
    definition -- grants, inserts, `SET` options -- stay attributed to the file.
    """
    text = source.decode("utf8", "replace")
    tree = EntityTree(path=path, language="sql", root_id=path)

    def line_of(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    # Hashes are taken over text with comments blanked, so a reworded header is
    # noise. String literals survive: changing a RAISERROR message is a change.
    hashable = _blank_noise(text, strings=False)

    found = definitions(text)
    batches = [m.start() for m in _BATCH.finditer(_blank_noise(text))]

    def end_of(index: int, start: int) -> int:
        limits = [len(text)]
        if index + 1 < len(found):
            limits.append(found[index + 1][0])
        limits.extend(b for b in batches if b > start)
        return min(limits)

    spans = [(offset, end_of(i, offset)) for i, (offset, *_r) in enumerate(found)]

    # The file entity owns only what no definition claims -- grants, inserts,
    # `SET` options. Hashing the whole file instead would report the file as
    # changed alongside the procedure that actually changed, and one edit would
    # be counted twice.
    outside, at = [], 0
    for start, end in spans:
        outside.append(hashable[at:start])
        at = end
    outside.append(hashable[at:])
    own = " ".join("".join(outside).split())

    tree.add(Entity(
        id=path,
        kind=EntityKind.MODULE,
        name="<module>",
        path=path,
        start_line=1,
        end_line=max(1, text.count("\n") + 1),
        raw_hash=digest(text),
        content_hash=digest(own),
        signature_hash="",
        body_hash=digest(own),
        body_size=len(own.split()),
        parent_id=None,
        synthetic=True,
    ))

    seen: dict[str, int] = {}
    for index, (offset, keyword, name, kind) in enumerate(found):
        end = spans[index][1]
        body = text[offset:end]
        words = hashable[offset:end].split()
        # The signature is the definition line: enough to tell "the parameters
        # changed" from "the body changed" without parsing the parameter list.
        head = hashable[offset:end].split("\n", 1)[0]

        base = f"{path}::{name}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        entity_id = base if count == 0 else f"{base}#{count + 1}"

        tree.add(Entity(
            id=entity_id,
            kind=kind,
            name=name,
            path=path,
            start_line=line_of(offset),
            end_line=line_of(max(offset, end - 1)),
            raw_hash=digest(body),
            content_hash=digest(" ".join(words)),
            signature_hash=digest(" ".join(head.split())),
            body_hash=digest(" ".join(words)),
            body_size=len(words),
            parent_id=path,
        ))
    return tree
