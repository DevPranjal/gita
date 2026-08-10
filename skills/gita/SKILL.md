---
name: gita
description: Read code changes as context diffs instead of line diffs. Use whenever you need to understand what changed in a git repository — reviewing a commit or PR, deciding what to re-test, checking whether a public API broke, or orienting yourself in unfamiliar changes. Replaces `git diff`, `git show` and `git log -p` for comprehension. A single `gita diff` returns the summary, the changed files, the changed functions and the relevant code, with formatting noise removed, and is never larger than the `git diff` it replaces. Triggers include "what changed", "review this commit", "did this break anything", "summarise this PR", or any point where you were about to read a raw diff.
---

# gita — context diffs for agents

`git diff` answers *which bytes moved*. You almost always need *which named
things changed, and can that break a caller*. gita answers the second question.

A moderate commit is 4,000–20,000 tokens of raw diff, most of it reformatting,
comment edits and unchanged context. gita gives you the same understanding for a
fraction of that — in one command, with the code included.

## Core rule

**Never read a raw `git diff` to understand a change. Run `gita diff` instead.**

One call gives you the summary, the changed files, the changed functions **and the
relevant code**. You should not need a second command. gita is never larger than
the `git diff` it replaces, and is usually far smaller.

```bash
gita diff <base> <head>
```

If the budget forced anything out, the output says so and names what is missing.
Only then is a follow-up worth it — every extra command costs a whole turn of
re-sent context, which is far more expensive than the tokens it saves.

## Commands

All commands accept `-C <path>` for the repository.

```bash
gita diff HEAD^ HEAD               # the last commit (the default)
gita diff HEAD                     # uncommitted work, like plain `git diff`
gita diff main HEAD                # a branch or PR range

gita diff <base> <head> --interface-only   # only changes that can break a caller
gita diff <base> <head> --patch            # plain unified diff, noise removed
gita diff <base> <head> --brief            # summary only, no code
gita diff <base> <head> --budget N         # cap output at N tokens
gita diff <base> <head> --json             # machine-readable

gita history <entity>              # how one function changed over time
gita show <entity>                 # exact hunks for one entity
gita savings                       # what this cost vs a raw git diff
```

Aliases: `darshan` (diff), `shloka` (show), `katha` (history), `vistaar` (expand).

### Answering common questions

| Question | Use | Exact? |
| --- | --- | --- |
| What changed here? | `gita diff <base> <head>` | yes |
| Did the public API break? | `gita diff <base> <head> --interface-only` | **yes** — from signature hashes |
| What changed in the auth code? | `gita diff <base> <head> --filter auth` | yes — name/path match |
| When did this function change? | `gita history <entity>` | yes |
| What should I re-test? | **not supported** | needs a call graph; do not guess |

gita has no natural-language question interface, on purpose. It reports facts it
computed and refuses questions it cannot answer exactly.

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
gita diff HEAD^ HEAD
```
That is the whole workflow. Summary, files, functions and code in one response.

**Check for a breaking change**

```bash
gita diff HEAD^ HEAD --interface-only
```

**Review a pull request**

```bash
gita diff origin/main HEAD
```

**When you want a plain diff, just smaller**

```bash
gita diff HEAD^ HEAD --patch
```
Ordinary unified diff format with formatting-only changes removed. Useful when
you want to read hunks directly and do not want a new output shape.

## Budgets

`--budget N` is a hard cap, honoured exactly at every value including zero.
gita degrades by dropping detail for lower-ranked entities and rolling the
summary up, so a small budget still describes the whole change.

The default is deliberately generous. Withholding detail to save a few hundred
tokens is a false economy: a follow-up command costs a whole turn of re-sent
context, which is orders of magnitude more expensive. Raise the budget rather
than making two calls.

## MCP

If gita is available as an MCP server, the same layers are exposed as
`gita_diff`, `gita_expand`, `gita_show` and `gita_savings`. `gita_diff` takes
`filter` and `interface_only`. Every result carries a `next` field naming the
entities you can act on — follow it rather than guessing entity ids.

Start `gita serve` (alias `sarathi`) to run the server over stdio.

## Limits — read before trusting output

- Change **kinds are exact**; gita computes them from parsed syntax, not from a
  model, so it will not invent an entity that does not exist.
- gita reports **what changed, not why**. It has no intent narrative yet.
- It does **not yet compute callers or blast radius**, so "what does this affect"
  is answered by name matching, not by a call graph. Verify before relying on it.
- Entity-level detail covers Python, JavaScript, TypeScript, TSX, Go, Rust,
  Markdown, YAML, JSON and TOML. **Any other text file is reported as a single
  whole-file change** — you will see that it changed, but not where. Use
  `gita show` or fall back to `git diff` for those.
- **Binary files are skipped entirely.** Check `files_changed` against
  `git diff --name-only` if a change seems to be missing.
- A helper that is moved *and* edited in the same commit may report as an
  addition plus a deletion rather than a move.
- `gita history` walks commits one at a time, so a large `--limit` is slow. It
  also does not follow an entity across a rename yet.
