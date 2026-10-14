# gita: git, reimagined for a world of agent coders

*Don't pay to understand the code you wrote.*

Coding agents spend tokens reconstructing context from files and diffs. **Gita
gives them reusable, evidence-backed code context directly from Git**: persistent
entity identity, committed entity history and exact historical source.
Indexed operations are the default. The old structural-review engine is retained
only behind an explicit transitional opt-in.

The agent owns the objective, planning and execution. Gita supplies the evidence.
No model, cloud account or inference API key required.

## Results

In a held-out review evaluation on [AACR-Bench](https://github.com/alibaba/aacr-bench),
covering **135 paired PRs across 46 repositories**:

| Measure | Result |
| --- | --- |
| Reported Copilot credits | **23.1% lower** (95% interval: 18.0-27.1%) |
| Serialized tool-output tokens | **18.7% fewer** (diagnostic count, not billed tokens) |
| Macro reference recall | 10.00% baseline / 9.69% Gita; met the predeclared five-percentage-point non-inferiority margin |

This historical study used GPT-5.6 Sol and an older legacy Gita revision, not the
default indexed product in this release.
It did **not** establish lower input-token use or equal quality: PRs with a
reference match fell from 64 to 57, and cache behavior affected credit results.
See [methodology, uncertainty and negative results](evals/RESULTS.md).

## Install

Requires **Git and Python 3.11+**. Install from source:

```sh
git clone https://github.com/DevPranjal/gita.git
cd gita
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS, or
`.\.venv\Scripts\Activate.ps1` in PowerShell. Then:

```sh
python -m pip install -c constraints.txt -e ".[mcp]"
python -c "from tree_sitter_language_pack import download; download(['python','javascript','typescript','tsx','csharp','cpp','go','rust','json','yaml','toml','markdown'])"
```

Grammar provisioning uses the network once; indexed operations then use local
Git objects. Omit `[mcp]` for CLI-only use.

## Use from the CLI

Run inside the repository you want to inspect, or add `-C <repo>`.
Replace `MyFunction` with a real name; `HEAD~1` requires a preceding commit.

```sh
gita index --status
gita index --scope project
gita id MyFunction --scope project
gita history MyFunction --scope project
gita history HEAD~1 HEAD --scope project --brief
```

Repeat `gita index --scope project` after new commits to update incrementally.
First builds traverse first-parent history; for large repositories, start with
the [bounded-scope walkthrough](docs/QUICKSTART.md).
Indexed reads never build an index on demand.

### Transitional legacy opt-in

`GITA_ENABLE_LEGACY` is disabled when unset or exactly `0`; exactly `1` enables
the old product. All other values, including an empty string, cause an
`invalid_configuration` error. Configure it in the process environment:

```powershell
$env:GITA_ENABLE_LEGACY = "1"
gita diff HEAD~1 HEAD
gita history MyFunction --legacy --brief
Remove-Item Env:GITA_ENABLE_LEGACY
```

Disabled `diff`/`show`/`expand` (including their aliases and `diff --indexed`)
return `legacy_disabled` with migration guidance. They never silently become
indexed endpoint diffs. Ordinary `history`/`katha` uses the published index even
with legacy enabled; `--indexed` remains a compatibility no-op. Only `--legacy`
selects reconstructing history; it cannot be combined with indexed selectors.
Root legacy Python exports are lazy and gated; supported low-level parser
helpers remain available to the index.

This is the first feature-flag increment, not complete retirement. Legacy code
still ships; query parity, independent verification, endpoint comparison and
index qualification remain follow-up work. Parser/matcher policy and storage
formats are unchanged by this increment; an already-incompatible index still
requires explicit handling.

## Connect an agent

Any agent with shell access can use the CLI. MCP clients can launch **`gita serve`**
over stdio; set `GITA_REPO` to the absolute path of the repository to inspect.
Use the installed environment's absolute `gita` executable path if it is not on
your client's `PATH`.

| Task | MCP tools |
| --- | --- |
| Build or inspect the index | `gita_index` (read-only status by default; explicit build) |
| Resolve identity and history | `gita_id`, `gita_history`, `gita_state` |
| Retrieve exact source or file entities | `gita_source`, `gita_entities` |

An indexed investigation follows **index → identity → history/state → source**.
Typed tools expose supported budgets, coverage and continuation cursors.
`gita_history` is indexed by default. Legacy `gita_diff`, `gita_expand` and
`gita_show` are absent from discovery unless the server starts with
`GITA_ENABLE_LEGACY=1`. Reconstructing history additionally requires `legacy=true`.
[Setup and tool details](docs/QUICKSTART.md#mcp-tools).

## How it works

Gita separates **what code is from what it is called and where it lives**.

- **Tree-sitter** extracts structural evidence rather than treating every changed line equally.
- **Persistent entity IDs** connect supported edits, renames and moves through deterministic matching. Copies get separate identities; uncertain matches stay explicit.
- **SQLite with immutable generations** stores evidence once for reuse, incremental updates and generation-pinned queries.
- **Git remains the source of truth** for exact historical source. Indexed history preserves intermediate changes, including reverts; it is not a net endpoint diff.

Indexed languages: Python, JavaScript, TypeScript/TSX, C#, C++, Go, Rust,
Markdown, JSON, YAML and TOML. Unsupported files and extraction gaps appear in
coverage, not as proof that nothing exists.

## Try the local viewer

```sh
python -m gita.web --create-demo
```

Open **http://127.0.0.1:7357** to explore a fictional repository with renames,
moves and reverted edits. The read-only viewer runs locally, with no frontend
build or CDN. The default viewer exposes indexed entities, history and source.
Legacy compare/patch and CLI-playground routes refuse without opt-in and their
controls are hidden. Restart with `python -m gita.web`; the scaffold refuses existing
directories.

## Boundaries and documentation

Indexed history covers **committed first-parent scope**, not uncommitted edits or
every branch of a merge. Structural evidence does not prove semantic equivalence,
merge safety or test correctness. Independent index verification is not yet
available (`gita index --verify` reports `verification_unavailable`).
Telemetry is opt-in.

[Quickstart](docs/QUICKSTART.md) · [Technical guide](docs/TECHNICAL.md) ·
[Identity and storage contracts](docs/FOUNDATION.md) · [Evaluation harness](evals/README.md) ·
[Changelog](CHANGELOG.md) · [MIT license](LICENSE)
