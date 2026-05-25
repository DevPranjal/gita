# gitpp v0.1 — Specification

Status: **draft**. The pivot, in one line:

> Don't reinvent `git merge`. Reinvent the **diff** and the **commit**
> so an agent can read them.

`git diff` and `git log` were designed for a human glancing at a terminal.
Coding agents pay for every token they read and reason in symbols, not in
unified-diff hunks. `gitpp` is a content-addressed store on top of LibCST
that emits **structural manifests** at commit time — typed operations on
named symbols — that an agent can consume in tens of tokens instead of
hundreds, filter by category, and trace across history.

The three pillars:

1. **Semantic diff.** A `gitpp diff` is a list of typed ops (`add_symbol`,
   `rename_symbol`, `modify_body`, `add_import`, …) on named symbols, not
   a textual hunk. Renames are first-class.
2. **Intent-rich commits.** Every commit persists its manifest as a
   separate addressable object. `gitpp explain <ref>` reads it back
   without re-parsing.
3. **Queryable history.** `gitpp symbol-log <name>` and
   `gitpp callers <name>@<ref>` traverse the stored manifests instead of
   re-parsing N revisions.

Semantic merge falls out of the same model as a bonus, and is documented
in §5 — but it is no longer the headline. The bar v0.1 has to clear is
"an agent reads a `gitpp explain` and a `gitpp diff` and can act on them
with no further parsing."

## 1. The agent-facing diff (Pillar 1)

A diff is a **manifest** — a single JSON object describing one or more
changed files at the granularity of named symbols.

### 1.1 Manifest schema

```jsonc
{
  "kind": "manifest",
  "schema": 1,
  "from": "<commit sha or null>",
  "to":   "<commit sha or null>",       // null = working tree
  "files": [
    {
      "path": "users.py",
      "status": "added | removed | modified",
      "ops": [ /* see §1.2 */ ]
    }
  ],
  "summary": {
    "logic_ops": 0,        // body / add_symbol / remove_symbol
    "signature_ops": 1,    // rename / modify_signature / add|remove_import
    "cosmetic_ops": 0,     // reorder_imports / format_only
    "symbols_touched": 2
  }
}
```

The summary is what an agent reads first to decide whether the change is
worth reading at all.

### 1.2 Op kinds (closed set)

Operations name **what changed** to **which symbol**, not which lines:

| Op                   | Fields                                                             | Category   |
|----------------------|--------------------------------------------------------------------|------------|
| `add_symbol`         | `name, kind` (function/class/...)                                  | logic      |
| `remove_symbol`      | `name`                                                             | logic      |
| `modify_body`        | `name, lines_added, lines_removed, summary` (first ±lines, capped) | logic      |
| `rename_symbol`      | `from, to, references` (call sites updated in same file)           | signature  |
| `modify_signature`   | `name, old_signature, new_signature`                               | signature  |
| `add_import`         | `module, names`                                                    | signature  |
| `remove_import`      | `module, names`                                                    | signature  |
| `reorder_imports`    | (no fields)                                                        | cosmetic   |
| `format_only`        | (no fields — file changed but the canonical structure did not)     | cosmetic   |

The op set is closed. New ops require a schema bump.

### 1.3 Rename detection

Renames are recovered, not stored. The algorithm:

1. For each file, parse with LibCST and collect top-level symbols
   (`FunctionDef`, `ClassDef`) keyed by name.
2. Match by name across the two sides. Anything unmatched is a candidate.
3. For each unmatched-on-left symbol, render its body **with its own name
   neutralized to `__SYM__`** and hash it. Compare against unmatched-on-right.
4. Hash match ⇒ emit `rename_symbol(from, to, references=N)` instead of
   `remove_symbol + add_symbol`.
5. Build a substitution map `rename_subs = {old → __RENAMED_k__,
   new → __RENAMED_k__}` for each accepted rename. When comparing the
   bodies of *other* symbols, apply this map first. This stops call-site
   updates from showing up as a spurious `modify_body` op on every
   caller. **This is the load-bearing trick.**

GumTree-style tree matching is deferred to v0.2 — body-hash plus
substitution clears all three v0.1 scenarios.

### 1.4 CLI

```
gitpp diff [<from-ref> [<to-ref>]] [--only logic|signature|cosmetic]
                                   [--exclude logic|signature|cosmetic]
                                   [--json]
```

Default is `HEAD` → working tree. `--only` and `--exclude` filter by
category — an agent debugging a regression reads `--only logic`, a code
reviewer reads `--exclude cosmetic`.

## 2. Intent-rich commits (Pillar 2)

Every `gitpp commit` does two things:

1. Computes the manifest from `parent.tree → new index` exactly as
   `gitpp diff` would.
2. Writes the manifest as a separate `manifest` object and embeds its
   sha in the commit object's new `manifest` field.

```jsonc
// commit object
{
  "kind": "commit",
  "tree": "<sha>",
  "parents": ["<sha>"],
  "message": "rename get_user to fetch_user",
  "timestamp": "...",
  "manifest": "<sha>"        // new in v0.1
}
```

The manifest is not free-form prose, and unlike the commit message it is
**verifiable** — if the recorded manifest disagrees with the tree-diff
between `parent` and `commit.tree`, the commit is corrupt.

### 2.1 `gitpp explain`

```
gitpp explain <ref> [--only ...] [--exclude ...] [--json]
```

Prints the persisted manifest. For commits written before v0.1 (no
`manifest` field), falls back to computing one on the fly from the
parent → self tree diff, so old history degrades to "slow but correct"
instead of "broken."

## 3. Queryable history (Pillar 3 — not yet built)

Planned in v0.1:

```
gitpp symbol-log <name>            # commits whose manifest mentions <name>
gitpp callers <name>[@<ref>]       # call sites of <name> in the named tree
```

Both read from stored manifests where possible. `callers` does one CST
pass over the target tree's files (O(target tree), not O(history)).

## 4. Object model and storage

`gitpp` stores four kinds of objects, all content-addressed by `sha256`
of canonical JSON (`sort_keys=True, separators=(",",":"),
ensure_ascii=False`).

| Kind       | Contents                                                                       |
|------------|--------------------------------------------------------------------------------|
| `file`     | `{kind: "file", content: <utf-8 source>}`                                      |
| `tree`     | `{kind: "tree", entries: [{path, file: <sha>}]}`                               |
| `commit`   | `{kind: "commit", tree, parents, message, timestamp, manifest?: <sha>}`        |
| `manifest` | The schema in §1.1                                                             |

There is no "blob" kind separate from `file` — Python source is the only
content type v0.1 understands.

On-disk layout:

```
.gitpp/
  HEAD                    # symbolic ref → refs/heads/<name>
  refs/heads/<name>       # sha of tip commit
  index                   # staged {path: sha} map
  objects/aa/bbbb...      # content-addressed object store
```

Per-node addressing — i.e. storing CST nodes as their own objects with
stable IDs — is planned for v0.2 and is **not** required by the manifest
or the merge model below. The current model parses source on demand
inside `diff_sources`.

## 5. Semantic merge (bonus, from the same model)

Because the manifest names operations on symbols, the obvious "AST merge"
falls out: for each symbol, apply both sides' ops; conflict if and only
if both sides' ops on the same symbol are not commutative.

v0.1 ships a working `merge_modules` for the **parallel-methods**
scenario — two sides add different methods to the same class, gitpp
takes the union; git conflicts because the closing lines of the class
moved. The other two scenarios from the original spec
(`import-reorder-add` and `rename-vs-edit`) remain `xfail` for merge in
v0.1 — `gitpp diff` and `gitpp explain` *do* describe them correctly, so
the agent-facing pillars hold; only the auto-merge for those two
patterns is deferred to v0.2.

This is intentional: the v0.1 acceptance bar is

> the agent can read what happened and act on it,

not

> the merge is fully automatic in every scenario.

## 6. CLI surface (v0.1)

```
gitpp init [<path>]
gitpp add <path>...
gitpp commit -m <intent>
gitpp log
gitpp diff [<from-ref> [<to-ref>]] [--only/--exclude] [--json]
gitpp explain <ref> [--only/--exclude] [--json]
gitpp merge <ref>                         # parallel-methods only in v0.1
gitpp symbol-log <name>                   # planned, pillar 3
gitpp callers <name>[@<ref>]              # planned, pillar 3
```

## 7. Out of scope for v0.1

- Per-node content-addressing and stable IDs as a stored field (deferred
  to v0.2; current rename detection is body-hash based and runs at diff
  time).
- Cross-file rename / callers-across-files.
- Languages other than Python.
- Agent identity, multiverse branching, environment snapshots, MCP,
  git interop.
