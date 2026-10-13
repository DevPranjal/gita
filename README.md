# gita: git, reimagined for a world of agent coders

*Don't pay to understand the code you wrote.*

Coding agents spend tokens reconstructing context from files and diffs. **Gita
gives them reusable, evidence-backed code context directly from Git**: structural
diffs, persistent entity identity, function history and exact historical source.

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

This historical study used GPT-5.6 Sol and an older Gita revision, not this release.
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
gita diff                                      # uncommitted changes
gita diff HEAD~1 HEAD --budget 4000 --json       # bounded structural evidence
gita diff HEAD~1 HEAD --filter MyFunction       # focus on one entity
gita show path/to/file.py::MyFunction HEAD~1 HEAD
```

Build reusable identity and history explicitly:

```sh
gita index --scope project
gita id MyFunction --scope project
gita history MyFunction --indexed --scope project
gita history HEAD~1 HEAD --indexed --scope project --brief
```

Repeat `gita index --scope project` after new commits to update incrementally.
First builds traverse first-parent history; for large repositories, start with
the [bounded-scope walkthrough](docs/QUICKSTART.md).
Indexed reads never build an index on demand.

## Connect an agent

Any agent with shell access can use the CLI. MCP clients can launch **`gita serve`**
over stdio; set `GITA_REPO` to the absolute path of the repository to inspect.
Use the installed environment's absolute `gita` executable path if it is not on
your client's `PATH`.

| Task | MCP tools |
| --- | --- |
| Inspect structural changes | `gita_diff`, `gita_expand`, `gita_show` |
| Build or inspect the index | `gita_index` (read-only status by default; explicit build) |
| Resolve identity and history | `gita_id`, `gita_history`, `gita_state` |
| Retrieve exact source or file entities | `gita_source`, `gita_entities` |

An indexed investigation follows **index → identity → history/state → source**.
Typed tools expose supported budgets, coverage and continuation cursors.
For uncommitted MCP diffs pass `base="HEAD", head=null`; MCP otherwise defaults
to the last commit. [Setup and tool details](docs/QUICKSTART.md#mcp-tools).

## How it works

Gita separates **what code is from what it is called and where it lives**.

- **Tree-sitter** extracts structural evidence rather than treating every changed line equally.
- **Persistent entity IDs** connect supported edits, renames and moves through deterministic matching. Copies get separate identities; uncertain matches stay explicit.
- **SQLite with immutable generations** stores evidence once for reuse, incremental updates and generation-pinned queries.
- **Git remains the source of truth** for exact historical source. A diff compares endpoints; indexed history preserves intermediate changes, including reverts.

Indexed languages: Python, JavaScript, TypeScript/TSX, C#, C++, Go, Rust,
Markdown, JSON, YAML and TOML. Unsupported files and extraction gaps appear in
coverage, not as proof that nothing exists.

## Try the local viewer

```sh
python -m gita.web --create-demo
```

Open **http://127.0.0.1:7357** to explore a fictional repository with renames,
moves and reverted edits. The read-only viewer runs locally, with no frontend
build or CDN. Restart with `python -m gita.web`; the scaffold refuses existing
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
