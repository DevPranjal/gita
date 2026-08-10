---
name: gita
description: Read code changes as context diffs instead of line diffs. Use whenever you need to understand what changed in a git repository — reviewing a commit or PR, deciding what to re-test, checking whether a public API broke, or orienting yourself in unfamiliar changes. Replaces `git diff`, `git show` and `git log -p` for comprehension, and costs 50-100x fewer tokens. Triggers include "what changed", "review this commit", "did this break anything", "summarise this PR", "what should I re-test", or any point where you were about to read a raw diff.
---

# gita — context diffs for agents

`git diff` answers *which bytes moved*. You almost always need *which named
things changed, and can that break a caller*. gita answers the second question.

A moderate commit is 4,000–20,000 tokens of raw diff, most of it reformatting,
comment edits and unchanged context. The same commit is **~30 tokens** as a gita
headline and **~500** in full. Read the headline first; pay for detail only where
it matters.

## Core rule

**Never read a raw `git diff` to understand a change. Start with `gita diff`.**

Reach for a raw diff only when you need byte-exact context that `gita show`
cannot give you — which is rare.

## The four steps, cheapest first

| Step | Command | Cost | Use when |
| --- | --- | --- | --- |
| 1 | `gita diff` | ~30–500 tokens | always start here |
| 2 | `gita expand <entity>` | ~50 tokens | a line says `(+N nested)` and you need the children |
| 3 | `gita show <entity>` | ~200 tokens | you must read the actual code |
| 4 | `gita ask "<question>"` | ~200 tokens | you have a specific question |

Stop as soon as you can answer the question. Most reviews never reach step 3.

## Commands

All commands accept `-C <path>` for the repository, and default to `HEAD^ HEAD`.

```bash
gita diff                          # last commit
gita diff main HEAD                # a branch or PR range
gita diff --budget 300             # cap the cost; the number is honoured exactly
gita diff --json                   # machine-readable, includes entity ids

gita expand "src/app.py::Store"    # children of a rolled-up entity
gita show   "src/app.py::Store::get"   # exact hunks for one entity
gita ask    "did the public API break?"
gita savings                       # what this cost vs a raw git diff
```

Each command has a Gita-inspired alias, if you prefer them:
`darshan` (diff), `vistaar` (expand), `shloka` (show), `prashna` (ask).

## Reading the output

```
3 files · 12 changes · 2 interface · 638 noise filtered
top: _CollectErrors, do_teardown_request, AppContext

src/flask/helpers.py::_CollectErrors  (+4 nested)
src/flask/app.py::Flask::do_teardown_request  [body_changed]
src/flask/ctx.py::AppContext::pop  [body_changed]
```

- **Line 1** is the headline. `noise filtered` is what gita suppressed —
  formatting, comments, import reordering. You do not need to see it.
- **`(+4 nested)`** means the entity has changed children rolled up. Use
  `gita expand` on that id to see them.
- **`src/app.py::Class::method`** is an entity id. Pass it verbatim to
  `expand` or `show`.

### Change kinds

| Kind | Meaning | Can it break a caller? |
| --- | --- | --- |
| `signature_changed` | parameters or return type changed | **yes** |
| `removed` | entity is gone | **yes** |
| `renamed` | same code, new name | **yes** |
| `added` | new entity | no |
| `moved` | same code, new location | no |
| `body_changed` | behaviour changed, interface intact | no |

`cosmetic` and `unchanged` are filtered out before you ever see them.

## Worked examples

**Review a commit**

```bash
gita diff
```
Read the headline. If it answers the question, stop.

**Decide what to re-test**

```bash
gita ask "what should I re-test?"
```
Focus on `signature_changed` and `removed` entities and their tests.

**Check for a breaking change**

```bash
gita diff --json | jq '.changes[] | select(.interface == true)'
```

**Review a pull request**

```bash
gita diff origin/main HEAD --budget 800
```

**Work down to the code**

```bash
gita diff                                  # 1. headline
gita expand "src/flask/helpers.py::_CollectErrors"   # 2. children
gita show "src/flask/helpers.py::_CollectErrors::__exit__"  # 3. the code
```

## Budgets

`--budget N` is a hard cap, honoured exactly at every value including zero. gita
adapts by rolling entities up rather than cutting the list off arbitrarily, so a
small budget still describes the whole change — just less finely.

Rules of thumb: **300** to orient, **1000** to review (default), **3000** for a
large PR you must understand in full.

## MCP

If gita is available as an MCP server, the same layers are exposed as
`gita_diff`, `gita_expand`, `gita_show`, `gita_ask` and `gita_savings`. Every
result carries a `next` field naming the entities you can act on — follow it
rather than guessing entity ids.

Start `gita serve` (alias `sarathi`) to run the server over stdio.

## Limits — read before trusting output

- Change **kinds are exact**; gita computes them from parsed syntax, not from a
  model, so it will not invent an entity that does not exist.
- gita reports **what changed, not why**. It has no intent narrative yet.
- It does **not yet compute callers or blast radius**, so "what does this affect"
  is answered by name matching, not by a call graph. Verify before relying on it.
- Supported languages: Python, JavaScript, TypeScript, TSX, Go, Rust. Files in
  other languages are skipped silently — check `files_changed` if a change seems
  to be missing.
- A helper that is moved *and* edited in the same commit may report as an
  addition plus a deletion rather than a move.
