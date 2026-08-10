# gita — Scoping Document

> *every agent needs a direction*
> git reimagined for a world of agent coders

**Status:** Draft v0.1 · **Date:** 2026-08-10 · **Context:** Hackathon — Zero-Cost Productivity track

---

## 1. Thesis

**The cheapest token is the one you never send.**

`git` was designed for humans reading line-by-line diffs. Agents inherit that format and pay for it
every single turn — a moderate PR is 5–20k tokens of syntactic noise, re-sent on every iteration of
an agent loop.

gita replaces the line diff with a **context diff**: a structured, layered description of *what
changed and what it affects*, assembled by a **small language model running locally on the user's own
device**. The expensive cloud model never sees the noise.

This yields three wins simultaneously:

| Win | Mechanism |
| --- | --- |
| **Cost** | >90% token reduction before the cloud round-trip. Local compression is $0.00. |
| **Privacy** | Source code and documents never leave the machine to be summarized. |
| **Speed** | No network dependency for the compression step; works fully offline. |

### Fit with the track

The track asks for on-device small models delivering outsized productivity gains at zero cloud cost.
gita's deviation is in *surface* (developer tooling rather than an Office add-in), not in *thesis*.
Scope L (§6, WS-7) extends the identical engine to documents, landing squarely back in the
information-worker lane:

> *Track Changes shows you words. gita shows you what changed in meaning.*

---

## 2. Design principle: deterministic core, probabilistic garnish

The single most important architectural commitment.

**The SLM does not compute the diff.** A deterministic pipeline extracts facts and structure; the
model is handed those facts and only writes prose over them.

```
DETERMINISTIC                                    │  PROBABILISTIC
─────────────────────────────────────────────────┼──────────────────────────────
 what symbols exist                              │  what the change means
 which symbols changed                           │  how changes cluster logically
 signature vs body change                        │  what the risk narrative is
 public API surface delta                        │  natural-language answers
 who calls what (with confidence label)          │
 what is formatting noise                        │
```

### The graceful-degradation property

If the model's prose is wrong, the layers beneath it are still exact and the agent can drill to
ground truth. **We degrade to "worse summary", never to "wrong facts."** This is what separates gita
from a prompt wrapper, and it should be stated explicitly in the pitch.

### Determinism tiers

| Tier | Capability | Mechanism | Confidence |
| --- | --- | --- | --- |
| 1 | Hunk → enclosing symbol attribution | tree-sitter + interval math | **Proven** — 94.0% coverage, 0.01% miss (§8) |
| 1 | Symbol inventory (fns, classes, exports, types) | tree-sitter `.scm` queries | **Proven** across 5 languages, 0 parse errors |
| 1 | Added / removed / body-changed / signature-changed | AST node comparison | Solid |
| 1 | Public API surface delta | export & visibility nodes | Solid |
| 1 | Noise filtering (format, import order, comments) | normalized AST equality | Solid, high value |
| 1 | Move detection (same body, new location) | content hash | Solid, trivial |
| 2 | Rename detection | body similarity + threshold | Reliable, tunable |
| 2 | Call graph / blast radius | tiered resolvers (below) | Labelled confidence — **now the main risk** |
| 3 | Intent, clustering, risk narrative | local SLM | Probabilistic by design |

### Blast radius: borrow a resolver, never build one

| Strategy | Source | Precision | Setup cost |
| --- | --- | --- | --- |
| `exact` | SCIP index (`scip-typescript`, `rust-analyzer --scip`) or live LSP `callHierarchy` | Compiler-grade | Per-language |
| `heuristic` | tree-sitter scope resolution + name matching | Good | None |
| `textual` | `git grep` on symbol name | Crude but effective | None |

Every edge in the fact graph carries its provenance label. Reproducible ⇒ deterministic, even when
approximate.

### Prior art (this is assembly risk, not research risk)

- **Aider repo-map** — tree-sitter + PageRank over a symbol graph. Ships today.
- **difftastic** — structural AST diff across ~30 languages.
- **SCIP / LSIF** — production code intelligence at Sourcegraph scale.
- **GitHub code navigation** — tree-sitter based.

---

## 3. The spine: one engine, two domains

```
Source ─→ Parser ─→ Entity Tree ─→ Entity Diff ─→ Fact Graph ─→ [local SLM] ─→ Context Layers ─→ Consumers
                   (stable IDs)                                                 L0 / L1 / L2
```

### Unified entity model

```
Entity {
  id            stable identity, e.g. "src/auth.ts::TokenStore::refresh"
  kind          module | class | function | type    (code)
                document | section | paragraph | table  (docs)
  name, path, range
  contentHash       body bytes, normalized
  signatureHash     interface bytes, normalized
  parent, children
}
```

> **Spike A constraint (§8):** the model must be defined over **bindings, not syntax categories.**
> In JS/TS most code lives in function *values* — callbacks, arrow functions, object methods — which
> are not declarations. Each language pack needs an explicit rule for naming an anonymous function
> after its binding site (variable, object key, assignment target, enclosing call). Ignoring this
> costs ~65 points of coverage. Each file also needs a synthetic `<module>` entity to own top-level
> statements.

A repository and a specification are both **trees of named things that change over time.** The same
differ runs over both: match on stable ID, compare hashes, classify the transition.

| Code | Document |
| --- | --- |
| function / class | section / subsection |
| body changed | paragraphs reworded |
| signature changed | heading renamed, section restructured |
| symbol moved | section reordered |
| public API delta | table cell / numeric delta |

Consequence: the document surface is **a second parser, not a second product** (~20% incremental
cost, not 100%).

For documents the deterministic layer is arguably *richer* than for code — `.docx` is zip + OOXML,
so the heading tree, moved sections, table cell deltas, and tracked changes (with author and
timestamp attribution) are all directly extractable. *"The budget figure changed from 1.2M to 900k"*
is a deterministic extraction, and is frequently the only fact anyone cared about.

Sequencing: **Markdown first** (heading tree, trivial) → `.docx` second.

### Progressive disclosure — the product in one idea

| Layer | Content | Typical cost |
| --- | --- | --- |
| **L0** | One-line intent per change cluster | ~50 tokens |
| **L1** | Symbol-level change list + impact / blast radius | ~300 tokens |
| **L2** | Actual hunks, on demand, scoped to one symbol | pay only when needed |

The agent starts at L0 and drills only where it must. This is fundamentally an **MCP tool design**:
the protocol surface *is* the product.

> **Spike A finding (§8): depth policy dominates compression.** A flat, full-depth symbol list
> achieves only 58.5% token reduction (34.3% on test-heavy JS, where a diff touches hundreds of
> nested callbacks). Rolling entity paths up to their first segment with a nested-count suffix
> achieves **96.1%**. L1 must therefore be **depth-adaptive under a token budget**, not a flat list.
> Rollup is core architecture, not an optimisation — which is precisely why WS-4 is on the critical
> path.

---

## 4. Target consumers

| Surface | Purpose | Priority |
| --- | --- | --- |
| **MCP server** | Real coding agent consumes L0/L1/L2 via tools | ★ Critical |
| **CLI** | Rich TTY output; human-facing credibility | ★ Critical |
| **Token-savings dashboard** | The proof artifact for judging | ★ Critical |
| VS Code extension | Inline context-diff view | Stretch |

---

## 5. Platform decisions

- **Implementation language: Python.** Spike A proved the tree-sitter path end-to-end here; there is
  a first-party MCP SDK; and CLI and dashboard are both well served. Re-litigating this in Node would
  reintroduce parsing risk we have already retired.
- **Inference:** Foundry Local / llama.cpp / Ollama.
- **Model candidates:** Phi-4-mini, Qwen2.5-Coder-3B, Gemma — subject to bake-off (WS-3).
- **⚠ Hardware constraint:** the current dev machine has **no GPU**. This blocks the WS-3 model
  bake-off and Spike B (§8). Everything on the critical path that is *not* inference — WS-1, WS-2,
  WS-4, WS-6, WS-8 — proceeds unaffected, since the deterministic core needs no model at all.
  Revisit once GPU hardware is available.
- **NPU:** *not* on the critical path. Local dGPU inference is still $0.00, still private, still
  offline. A DirectML / Windows ML path is a demo garnish to add later, not a dependency.
- **Parsing:** tree-sitter, via prebuilt language packs. Validated — 0% parse errors (§8).

---

## 6. Workstreams (max scope)

★ = critical path / spine.

| ID | Workstream | Components |
| --- | --- | --- |
| **WS-1 ★** | Core engine · **✓ built** | Entity model & stable IDs · tree-sitter integration · per-language query packs · entity differ · noise filters · move & rename detection · fact graph (`ChangeSet`) |
| **WS-2** | Resolution | SCIP ingestion · LSP bridge · heuristic scope resolver · grep fallback · confidence labelling · blast-radius ranking |
| **WS-3 ★** | Local inference · **⚠ blocked on GPU** | Runtime integration · model bake-off · fact-constrained prompting · grounded-generation guardrails · caching · *stretch:* NPU path |
| **WS-4 ★** | Context layers | L0/L1/L2 assembly · token budgeting · progressive-disclosure protocol · query-driven slicing (`gita context "<question>"`) |
| **WS-5** | Persistence | Idle-time incremental indexing on commit · index storage & invalidation · `gita why <symbol>` · offline semantic history search |
| **WS-6 ★** | Consumers | MCP server ★ · CLI ★ · dashboard ★ · VS Code extension (stretch) |
| **WS-7** | Document surface | Markdown parser · `.docx` OOXML parser · section tree differ · tracked-changes attribution · table & numeric deltas · doc-flavored layers |
| **WS-8 ★** | Evaluation & proof | Benchmark PR corpus · token accounting harness · answer-quality rubric · latency & cost telemetry · demo script |

**Cut line for a minimum winning demo:** WS-1 + WS-3 + WS-4 + WS-6 + WS-8.
WS-2, WS-5, WS-7 are upside.

**Current sequencing:** WS-3 is blocked on GPU hardware (§5). Everything else on the critical path —
WS-1, WS-4, WS-6, WS-8 — is inference-free and proceeds now. The deterministic core, the L0/L1/L2
assembly, the MCP surface, the CLI and the measurement harness can all be built and benchmarked
with a stub summariser in place of the model. Slotting a real SLM in behind the fact graph is a
late, contained integration — by design, since §2 forbids the model from touching the facts.

---

## 7. Success metrics

Measured on a fixed corpus of real PRs, answering a fixed question set
(*"does this break the public API?"*, *"what should I re-test?"*, *"summarize for the reviewer"*).

| Metric | Target |
| --- | --- |
| Token reduction vs raw `git diff` | **> 90%** — *96.1% structural headroom measured, §8* |
| Answer quality vs raw diff baseline | Equal or better |
| Hunk → symbol attribution accuracy | **> 95%** — *94.0% coverage / 0.01% miss, achieved §8* |
| Cloud cost | **$0.00** |
| Works with network disconnected | Yes — the demo beat |

---

## 8. De-risking spikes

### Spike A — attribution accuracy · **COMPLETE · PASS**

Ran against 66 real merge commits across `express` (JS), `flask` (Py), `gin` (Go), `got` (TS),
`ripgrep` (Rust) — 747 files, 28,595 changed lines, both sides of every diff.

| Metric | Result |
| --- | --- |
| Attribution coverage | **94.0%** |
| Real miss rate | **0.01%** (3 lines of 28,595) |
| Parse error rate | **0.0%** (0 of 747 files) |
| Token compression, rolled L1 | **96.1%** (355,551 → 13,876) |

The residual 6% is not error: classified, it is 99.8% blank lines, comments, imports and top-level
declarations. **The deterministic core is proven.** Three findings fed back into §2, §3 and §6.

→ *Full analysis, findings and limitations: [`SPIKE-A-RESULTS.md`](./SPIKE-A-RESULTS.md)*

The harness is retained as the permanent regression benchmark for WS-8.

### Spike B — grounding · **DEFERRED · blocked on GPU hardware**

Feed a fact graph to a small local model and measure whether it stays grounded in the supplied
facts — establishing the model floor before WS-3 commits to one.

**Blocked:** no GPU on the current dev machine (§5). Deferred until hardware is available.

Planned metrics:

| Metric | How measured | Hardware-dependent? |
| --- | --- | --- |
| Symbol hallucination rate | every symbol named in output must exist in the input fact set — pure string check | No |
| Coverage of material changes | did it surface the largest signature / API deltas? | No |
| Unsupported claim rate | judge pass over causal statements | No |
| Latency (TTFT, total) | must beat a network round-trip | **Yes** |
| Output determinism | drift across N runs on identical input | No |

Note that only latency genuinely requires a GPU. The grounding metrics are hardware-independent and
could be run on CPU with a quantised 1–3B model if we want an early read — slow, but the
hallucination numbers would still be valid. Splitting into **B1 (grounding, CPU-runnable)** and
**B2 (latency, GPU-required)** is available if the model floor becomes a blocking unknown before
hardware arrives.

If hallucination proves nonzero but bounded, the remedy is *not* a larger model — it is a
deterministic validator that rejects any output naming a symbol absent from the fact graph, and
retries. That preserves the graceful-degradation property in §2.

---

## 9. Open questions

**Resolved by Spike A**

- ~~Is the deterministic core achievable?~~ → Yes. 94.0% coverage, 0.01% miss, 0% parse errors.
- ~~Is parsing a robustness risk?~~ → No. Risk reallocated to resolution (WS-2).
- Language priority: JS/TS demands the most entity-model work; Python/Go/Rust are near-free.

**Open**

- What is the depth-adaptive rollup policy — fixed budget, or entropy-based?
- Does the MCP surface expose L2 as a separate tool call, or as a depth parameter on one tool?
- Index storage format for WS-5 — SQLite, or flat content-addressed files?
- For docs: is `.docx` mandatory for the pitch, or does Markdown suffice?
- Rename-detection similarity threshold — tune empirically on the corpus.
- **Blocked on hardware:** which SLM clears the grounding bar at acceptable latency? (Spike B)
- **Blocked on hardware:** what is the minimum viable model size — does 3B suffice, or is 7B the floor?

---

## 10. Naming

The project is named for the Bhagavad Gita — hence *every agent needs a direction*.

**Decision:** conventional verbs are canonical; Gita-inspired names are first-class aliases.
`gita diff` is what a judge or a new user will guess and what `--help` leads with; the alias carries
the identity. Discoverability wins the demo, identity wins the memory.

- Aliases are chosen **per command, as each is built** — not reserved up front.
- **Internal layer names stay `L0` / `L1` / `L2`.** Code and docs should read plainly; the poetry
  belongs on the surface, not in the call graph.

Candidate vocabulary, for reference when each command lands:

| Term | Meaning | Natural fit |
| --- | --- | --- |
| `darshan` | beholding, seeing truly | the context diff |
| `saar` | essence, gist | L0 headline |
| `shloka` | the verse itself | L2 exact hunks |
| `prashna` | question | ask about a change |
| `smriti` | memory | the local index (WS-5) |
| `kshetra` | the field (ch. 13) | survey of the codebase |
| `sarathi` | charioteer, guide | the MCP server |

`sanjaya` — who narrates the battlefield to a king who cannot see it — is the closest analogue to
what gita does, and is held in reserve.
