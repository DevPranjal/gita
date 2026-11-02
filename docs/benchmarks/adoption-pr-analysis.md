# Indexed-tool adoption: 21 completed ReviewBench PR pairs

## Scope and evidence

This analysis covers **42 valid B/H reviewer outputs for 21 PRs** in the artifact bundle `artifacts/gita-rb-live4-20261015`. The valid H / failed B orphan for Mcp-Docker #29 is excluded, as are later tasks without pairs. B is baseline; H has indexed tools available.

Evidence is observable tool calls, arguments, returned source/metadata/errors, and final findings. **Hidden model reasoning and reference-label text were not read or summarized.** Existing numeric per-PR scores were joined from `reviewbench-auto-indexed-quality.json`; no new quality judgment was run. Final finding topics below are reviewer assertions, not independently verified defects.

The companion [JSON report](adoption-pr-analysis.json) contains 21 machine-readable rows, tool-sequence references, source-retrieval paths, pagination/friction evidence, conditional opportunities, existing scores, and reproducible SHA-256 provenance bundles covering each pair's eight trace/output artifacts. Protocol, scopes and score-report digests are also recorded there.

No engine/index investigation, code execution, models, network access or commits were performed.

## Five findings that matter before the rerun

### 1. No completed indexed discovery-to-source workflow was observed

Across H's 115 calls, there was **one `entities` call, zero entity-source calls, zero endpoint-diff calls, and one failed `events` call**. Only 2/21 H tasks called any indexed tool, including status calls.

The entity lookup was **the last tool call** on avatar tests, after the implementation had already been read. It returned function IDs and spans, including `send_request_to_llm`; no source retrieval followed those IDs. All actual source retrieval used `raw_diff`, `read_file` or text `search`.

**Inference:** these traces do not demonstrate a useful indexed-source workflow, so they cannot establish an index benefit. They also do not reveal the reviewers' internal reasons for choosing raw tools.

**Fast improvement:** make changed-entity results directly actionable as exact endpoint source: path, span, a source action, and completeness/continuation status together. Validate the round trip before measuring adoption. Measure successful, relevant source retrieval—not a quota of indexed calls.

### 2. Small pages are a stronger friction signal than duplicated source

| Observable measure | B | H |
|---|---:|---:|
| All calls | 167 | 115 |
| Raw diff calls | 64 | 41 |
| File-read calls, including failures | 72 | 45 |
| Text searches | 16 | 15 |
| File listings | 8 | 8 |
| Results marked truncated | 84 | 52 |
| Truncated file-read results | 29 | 17 |
| Calls carrying cursor arguments | 36 | 17 |
| Tasks with any clipped diff page | 17 | 17 |
| Numbered source lines delivered | 4,914 | 2,904 |
| Repeated numbered source lines | 96 | 37 |

There were **81 raw diff responses exactly 3,000 characters long**. The 569-line installer took eight B / seven H file reads. H traversed `llm_client.py` in four reads; the first three were clipped. Neither arm used file-read cursor arguments in the valid pairs: reviewers manually advanced line ranges.

Only **133/7,818 delivered numbered source lines (1.7%)** repeated within a PR/arm/path. Overlapping *requested* ranges usually continued after a clipped result; calling all these reads redundant would be misleading.

**Fast improvement:** complete small-function source within a bounded budget, with explicit returned line bounds and executable continuation. Preserve coverage: fewer calls alone is not success.

### 3. Endpoint change maps are more immediately relevant than general history

Some first patch pages were dominated by docs/configuration:

- **Emdash #2:** reviewers pursued SEO batching after changeset metadata, despite the auth-provider PR title.
- **Nebula #224:** B explored system/process state; H explored metrics registration. Both stopped after a clipped global patch page beginning in work metadata.
- **Codebase-Conversion #1:** B explored fetch/download code; H explored credentials/retries after the devcontainer prefix.
- **PROTRIDER #10:** the patch prefix was documentation; relevant pipeline sections lay beyond H's clipped initial source reads.

These are observed differences in inspected scope, not proof of undiscovered defects. They explain why apparently shorter runs are not necessarily equivalent reviews.

**Fast improvement:** a compact changed-file/definition map with endpoint before/after source and explicit uncovered areas. Separate code from documentation/configuration. Do not substitute the PR title for the actual endpoint change inventory.

### 4. Keep the small-diff fast path; remove avoidable path guessing

H made **no file reads on 8/21 PRs**; two PRs used exactly one complete diff in both arms. Low adoption on these tasks is not inherently a product failure.

For deeper tasks, observable discovery mistakes matter:

- Both avatar runs first requested a nonexistent path missing the nested package directory.
- Both PROTRIDER runs began with an empty `protrider/**` listing before discovering `src/protrider`.
- Chronicle H sent a backslash-form repository path; the source tool explicitly required a repository-relative POSIX path. It recovered on the next read.
- Apilix B had two empty searches before reaching the relevant adapter/server source.

**Fast improvement:** exact reusable paths/source actions, clear glob behavior, and actionable path errors. Preserve traversal validation. Do not require entity/history calls when a complete diff already supplies the relevant context.

### 5. History should answer a temporal question, not be mandatory ceremony

The paired population used **eight raw `commit_log` calls and one raw `file_history` call**. Raw history calls exist in both arms and are not indexed-tool adoption.

The useful concrete history question was **tp #152 B#33**: whether `DeleteNoteCommand` was already a deferred-backend stub. The file-history result showed its introduction. By contrast, most other concerns—callback ordering, SQL pagination, path validation, mocks, trigger filters—are endpoint questions.

Gimmes H's `events` request was refused: **“No common first-parent generation; use raw_diff.”** The status responses actually inspected on avatar and Gimmes both reported `history_ready=false`; this is not extrapolated to unqueried tasks.

**Fast improvement:** disclose history availability before suggesting chronological operations; offer endpoint comparison when unavailable. Keep history conditional on provenance, introduction, or prior-schema questions.

## Per-PR evidence and plausible tool fit

`Calls` and `Reads` are B/H. Reads include failed attempts. “Fit” is an inference from the navigation task, not a promise that the current index supports it.

| PR | Observed review/change area | Calls | Reads | Concrete fit or boundary |
|---|---|---:|---:|---|
| TechDebtMCP #135 | Regex validation/normalization and input guards | 8/4 | 3/0 | Complete `validatePattern` / `executePattern` source plus handler; little history need. |
| Emdash #2 | Changeset prefix; SEO batching exploration | 5/5 | 1/2 | Endpoint change inventory first; then `getMany` and chunk helper source. |
| disaster-ninja-fe #1230 | Basemap style/remount and mock test | 1/1 | 0/0 | Complete diff was cheap; optional renderer lifecycle corroboration. |
| Apilix #12 | Workspace Git adapter/server routes | 9/5 | 4/0 | Shared `ensureRepo`, push/pull/timestamp source; avoid server-file paging. |
| aafactory #17 | New mock-chat test, unchanged implementation context | 10/11 | 4/4 | Resolve exact `send_request_to_llm` path; sole entity call came too late to drive source. |
| Codebase-Conversion #1 | Devcontainer prefix; fetcher versus LLM client | 5/7 | 3/5 | Changed-code map and complete retry/credential/download definitions. |
| node-postgres #3650 | Pool tracing callback/context | 3/3 | 1/1 | Complete connect wrapper plus diagnostic tests; source history adds little. |
| figlet #32 | CI/publish trigger filters across packages | 8/2 | 3/0 | Endpoint workflow inventory, not mandatory code entities. |
| CrabTrap #15 | Audit summaries/pagination and consumers | 6/7 | 0/3 | `QuerySummaries`, `Count`, scanner and caller contract source. |
| gh-copilot-review #9 | Review freshness and GitHub API pagination | 1/1 | 0/0 | Complete diff; optional REST helper context. Application commits API is not source-history usage. |
| agents-config #1 | Multi-tool installer layers/merge/hash | 9/8 | 8/7 | Complete shell functions/blocks, or file-source fallback if parsing is unavailable. |
| jadx-collaboration #3 | Pull/push worker and UI dispatch boundaries | 6/6 | 1/3 | Pull/push/UI helper/resolver source and test callers. |
| tp #152 | Date-note model/storage/UI and deletion stub | 34/13 | 14/3 | Endpoint command-to-storage trace; conditional introduction history for the old stub. |
| Nebula #224 | Work-metadata prefix; process versus metrics exploration | 8/6 | 5/2 | Changed-definition inventory before subsystem choice; registry wiring source for H's concern. |
| translator-ingests #336 | GtoPdb grouping/action mapping | 3/3 | 2/2 | Complete transform source instead of two clipped ranges. |
| Chronicle #14 | Storage validation/permissions/helper relocation | 13/7 | 7/5 | Resolve production versus test-copy helpers and `Storage::open`; exact source paths. |
| Pika #200 | Palettes/history migration, UI/export context | 7/3 | 1/0 | Migration/default representation/caller source; history only for a concrete older-schema question. |
| PROTRIDER #10 | README prefix; checkpoint load/gating source | 9/5 | 6/2 | Exact `src` paths and complete load/run definitions, not clipped file prefixes. |
| Gimmes #468 | Volume/OI/price staleness logic | 13/16 | 5/6 | State/model/adapter/caller endpoint source; unavailable events should route directly to endpoint diff. |
| ForgeMap #106 | Theme CSS and Pages concurrency | 7/1 | 3/0 | Workflow/CSS block comparison; code entities/history are low-priority. |
| LANdalf #88 | Ping overload/cancellation plus container utility | 2/1 | 1/0 | Complete small diff; optional outer service-loop source to verify cancellation intent. |

## Rerun decision

Prioritize **actionable endpoint source**, **complete bounded source/paging**, and **an accurate changed-code map**. Check exact-path recovery and history-unavailable fallback as small usability fixes. Retain raw tools and the small-diff path.

Use the source-heavy installer, note-storage, API-summary and checkpoint tasks as navigation checks based solely on their observable source work—not reference labels. Do not introduce label-tuned prompts. Existing per-PR precision/recall values are preserved in the JSON for outcome comparison; the 52-call H reduction and 42/39 final finding totals do **not** establish an indexing efficiency or correctness benefit.
