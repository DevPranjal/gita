# Quickstart

Requires Git and Python 3.11+. Install the current source from `main`;
package publication is separate.

```powershell
git clone https://github.com/DevPranjal/gita.git
cd gita
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e .
.\.venv\Scripts\python.exe -c "from gita.entities.parsers import get_parser; from tree_sitter_language_pack import downloaded_languages; names = ['python','javascript','typescript','tsx','csharp','cpp','go','rust','swift','php','java','kotlin','c','ruby','bash','powershell','hcl','dart','json','yaml','toml','markdown']; [get_parser(name) for name in names]; assert set(names) <= set(downloaded_languages())"
```

Grammar provisioning uses the network once. Indexed operations then use local
Git objects. On Linux/macOS, activate with `source .venv/bin/activate` and use
`.venv/bin/python` instead. If PowerShell activation is unavailable, invoke
`.\.venv\Scripts\gita.exe` wherever the guide uses `gita`.

For an existing installation, provision the new grammars explicitly:

```powershell
.\.venv\Scripts\python.exe -c "from gita.entities.parsers import get_parser; from gita.entities.languages import SPECS; from tree_sitter_language_pack import downloaded_languages; names = {spec.name for spec in SPECS}; [get_parser(name) for name in sorted(names)]; assert names <= set(downloaded_languages())"
```

For an isolated installation, set `GITA_PARSER_CACHE` to an absolute grammar-cache
directory before provisioning and keep it set for CLI/MCP and parsing workers.
New language rules change the parse policy; explicitly rebuild rather than
relabeling existing generations. Missing grammars are errors, not implicit
downloads during indexed operations. See [language boundaries](../README.md#how-it-works).

C# extensions are `.cs` and `.csx`. C++ extensions are `.cpp`, `.cc`, `.cxx`,
`.c++`, `.h`, `.hpp`, `.hh`, `.hxx`, `.h++`, `.ipp`, `.tpp` and `.inl`.
Indexing never downloads a missing grammar. Adding languages changes parser
policy fingerprints, so existing indexed scopes require an explicit
`gita index --scope <name> --rebuild`, preserving any previously selected boundary
with `--since <original-anchor>` when applicable. Retained generations are not
deleted automatically.

Use an existing repository and replace `YourFunction` with a real function name:

```powershell
$repo = 'C:\path\to\repository'
.\.venv\Scripts\python.exe -m gita -C $repo index --scope demo --ref HEAD
.\.venv\Scripts\python.exe -m gita -C $repo id YourFunction --scope demo
.\.venv\Scripts\python.exe -m gita -C $repo history YourFunction --scope demo --order oldest
```

History is indexed by default, even if legacy opt-in is enabled. `--indexed`
is a compatibility no-op. Read attempts without a published compatible index
refuse; they never create storage or parse source.

Legacy `diff`, `show`, `expand` and aliases (including `diff --indexed`) require
`$env:GITA_ENABLE_LEGACY = '1'`. Unset or exactly `0` disables them; all other
values are invalid, including empty strings. Disabled operations return
`legacy_disabled` with migration guidance, not indexed endpoint-diff results.
For old reconstructing history, also pass `--legacy` (MCP: `legacy=true`);
indexed scope/order/generation/cursor/at selectors cannot be mixed with it.
Examples of old structural review later in this guide are historical opt-in
workflows, not the default product. Legacy-dependent evaluation also requires
the flag; this transitional increment does not qualify the index or add parity,
independent verification or true endpoint diff.

Repeat the index command after new commits to resume incrementally. Use a full
returned EID when a name is ambiguous. Add `--json` for machine-readable output;
large answers include a continuation cursor. First builds inspect committed
history, so start with a small repository for a short demonstration.

For a large repository, an explicit current-tree scope avoids replaying its full
first-parent history:

```powershell
.\.venv\Scripts\python.exe -m gita -C $repo index --scope current --since HEAD --workers 4 --parse-timeout 30
.\.venv\Scripts\python.exe -m gita -C $repo index --scope current --workers 4 --parse-timeout 30
```

The first command indexes only the pinned tip. The second resumes from that tip;
do not repeat `--since HEAD` after new commits, because that changes the boundary.
Earlier history is outside this scope. Without `--since`, an initial build still
walks all available first-parent history.

Parsing defaults to up to four reusable isolated workers. `--workers 1` selects
serial execution; the supported range is 1-32. `--parse-timeout` is a finite,
positive per-file deadline (default five seconds), not a whole-build limit. It
includes worker startup and transport, but not time waiting in the work queue.
Timeouts fail the build and roll back new writes; they are never cached as an
empty parse. Increasing this limit does not make parsing faster.

For structural review in a repository with at least two commits:

```powershell
.\.venv\Scripts\python.exe -m gita -C $repo diff HEAD~1 HEAD --brief
```

Omit `--brief` for entity-owned hunks: parent sections exclude child edits,
shared-line changes remain visible, and parser-confirmed layout-only hunks are
suppressed. Use `--filter YourFunction` to narrow the result, `gita show` for
one entity including children, or `--patch` for the same targeted hunks in
diff form. Requested and raw-diff token caps still apply.

Standalone comments render as complete blocks. File filters retain independent
comment context; function filters do not acquire neighboring documentation.

## Entity or Range History

```powershell
gita history YourFunction
gita history HEAD~3 HEAD
gita history YourFunction --indexed --scope demo
gita history HEAD~3 HEAD --indexed --scope demo --json
```

One positional token always selects an entity, even if a Git ref has that name.
With two positional tokens, a first token that resolves as a Git revision
selects range history. Old entity-plus-revision forms require explicit `--legacy` and the environment
opt-in. Indexed entity history instead uses `--at`.

Ordinary entity history follows persistent identity and includes patches;
range history returns all recorded transitions in `(base, head]`,
oldest first. Both endpoints must belong to the selected first-parent index.
Equal endpoints return an empty result. This is event history, not a net diff:
an edit and its later reversal remain two transitions.

Indexed range history accepts `--limit`, `--budget`, `--scope`, `--generation`
and `--cursor`. Add `--brief` to either indexed history form for metadata only,
without reading historical source bytes. Resume using the returned pinned `range.base.hex` and
`range.head.hex`; changing endpoints invalidates the cursor. Indexed entity
history uses `--at` and supports either `--order oldest` or `--order newest`.
For compatibility, `diff --indexed` still renders range events on this branch;
plain `diff A B` is the net endpoint comparison.

Indexed history includes inline patches by default, generated from the exact
stored source/destination references without reparsing or reading the worktree.
Event metadata and pagination take priority; only complete patches that fit the
remaining serialized-output budget are included. Each event has `patch_status`:

- `included`: the `patch` field contains unified hunks with absolute file lines.
- `unchanged`: identical source bytes, for example a pure move.
- `not_applicable`: no proven before/after transition, for example an observed anchor.
- `omitted_budget`: increase the budget or retrieve the retained source/destination
	occurrences through `gita_source`. Page omission counts count events, not patches.
- `unavailable:source_unavailable` or `unavailable:non_utf8_source`: the event
	remains available, but a lossless text patch cannot be supplied.

Patches describe extracted entity byte spans. Newline markers describe those
spans, which can end before a file's line terminator; these are review hunks,
not a promise of an independently applicable whole-file patch. `--brief` omits
both patch fields and source reads. MCP `gita_history` has the same defaults and
supports `brief=true`.

## MCP Tools

Install the optional MCP dependency, then start `gita serve` from this version:

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[mcp]"
gita serve
```

Restart an already-running MCP server after upgrading to discover the new tools.

| Tool | Query |
| --- | --- |
| `gita_diff` | Legacy opt-in only: structural changes and the old indexed event adapter. |
| `gita_expand` | Legacy opt-in only: changed children of a container. |
| `gita_show` | Legacy opt-in only: exact changed hunks for an entity. |
| `gita_history` | Supply `entity`, or `base` and `head`; persistent evidence is the default, `indexed` is a compatibility no-op, `brief=true` returns metadata only. |
| `gita_index` | `action="status"` by default; explicit `build`, `verify` or `gc`. |
| `gita_id` | Resolve a name/locator/EID, including historical names. |
| `gita_state` | State and source occurrence for a full EID at a revision. |
| `gita_source` | Exact committed source from a returned occurrence. |
| `gita_entities` | Paginated occurrences in a repository-relative file at a revision. |

All tools accept a `repo` override. The three legacy tools are absent from
discovery unless the server starts with `GITA_ENABLE_LEGACY=1`; reconstructing
history additionally selects `legacy=true`. Opted-in uncommitted diffs use
`base="HEAD", head=null`; indexed reads exclude working-tree changes. An indexed workflow is
`gita_index(action="build")`, then `gita_id`, `gita_history` or `gita_state`,
and `gita_source`. Preserve the returned scope, generation and occurrence when
following references. Read-only queries never build missing indexes.

Source content carries `encoding` (`utf-8` or `base64`) and `byte_length`.
Budget overflow returns an error, not partial code. Indexed pages include
coverage, omission counts and continuation cursors. Index build/cleanup writes
local index storage only; `verify` still reports `verification_unavailable`.
