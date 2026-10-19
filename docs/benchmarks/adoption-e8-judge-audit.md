# E8 semantic judgment audit

**Conclusion: insufficient evidence to certify the judge as a sole adoption gate.**
Eight existing accepted candidate-reference edges were checked against allowed
HEAD Git objects. All eight have the same underlying cause and impact as their
matched issue; no semantic disagreement was found. This supports these particular
matches, not general judge reliability or a quality/cost gain.

Protocol: `cf8e342521e76dc04a7983e6db0d69f45f520f9547242b0449ad2a7ccfec0d2f`.
Run ID: `runs/e8-solmedium-dev3`.
Identifier SHA256: `c15d52b32aed270efbe8dea0d67d80bc387cd6b6577d0e25a5a87f0860d0d3e8`.
The [structured audit](adoption-e8-judge-audit.json) records exact task IDs,
HEAD/BASE commits, source checkout coordinates, candidate/arm mappings,
existing TP/FP labels, qualifications, and published-decision JSON pointers.
Artifact locations use stable relative IDs with identifier SHA256 values in the
JSON companion. These hashes identify the original location strings, not file
contents; no source or evidence artifact was reread for this publication update.
Git commit identities and the protocol digest remain unchanged.

## Method and evidence

Selection was purposive: three tasks, eight distinct mechanisms, and both shared
and arm-only coverage. Agreement required a common causal failure and impact,
not keyword or line overlap. Source was inspected directly using Git objects;
published source-context snippets were not treated as sufficient verification.
Existing matching evidence was visible first, so this was **not blinded**.

Every sampled reference already has label **TP**: eight TP, zero FP. Labels were
tracked, not replaced. No score was recalculated. No reference text,
reference-source metadata, or private reasoning is reproduced here.

In the table, C is the global candidate index in `match-input.json`; R is an
opaque existing reference index. Source citations refer to independently read
HEAD source, not reference locations. All verdicts have **high confidence within
their stated static-inspection limits**.

| Edge | Task / candidate / reference | Verdict | Independent causal evidence |
|---|---|---|---|
| E01 | p06 / B C0 / R23 (TP) | agree | Runtime sibling import versus publish allowlist: `packages/pg-pool/index.js:3`; `packages/pg-pool/package.json:46-50`. Same installed-package failure. |
| E02 | p06 / B C1 / R4 (TP) | agree | Object-query callback is optional, tracing requires it, and event completion exists: `packages/pg/lib/client.js:625-634,709-737`; `packages/pg/lib/query.js:130-149`. Same observability omission. |
| E03 | p06 / H C2 / R0 (TP) | agree | Timer captures the earlier callback, invokes it directly, and replaces the later traced callback: `packages/pg/lib/client.js:650-678,721-734`; `packages/pg/lib/query.js:130-133`. Same incomplete completion lifecycle despite different cited lines. |
| E04 | p08 / B C0 / R4 (TP) | agree | Live insertions shift descending database offsets; fixed-offset results append without deduplication: `web/src/hooks/useAuditLog.ts:52-61,67-84`; `web/src/stores/auditStore.ts:33-60`; `internal/admin/pg_audit_reader.go:386-393`. Same duplicate-row mechanism. |
| E05 | p08 / H C2 / R21 (TP) | agree | Expansion requests detail but the optional-store guard precedes dispatch; startup wiring is conditional: `web/src/components/AuditTrail.tsx:103-120`; `internal/admin/api.go:1041-1054`; `cmd/gateway/main.go:219-225`. Frontend and backend describe one blocked path. |
| E06 | p08 / B C3 / R2 (TP) | agree | Row-query failure becomes nil; count succeeds independently; handler normalizes and reports success: `internal/admin/pg_audit_reader.go:395-412`; `internal/admin/api.go:325-340`. Same error-propagation defect across layers. |
| E07 | p17 / H C0 / R16 (TP) | agree | Failed loading returns no model; fallback creates one; training checks file existence rather than load success: `src/protrider/pipeline.py:89-91,450-491,508-512`. Same skipped-training state error. |
| E08 | p17 / H C2 / R0 (TP) | agree | Divergent-loss branch assigns one local name and returns another: `src/protrider/model/model_helper.py:73-86`. Same unbound-local failure; exception-family wording does not alter identity. |

### Qualifications

- E01 was not checked by constructing a package.
- E03 tracing events were not observed dynamically.
- E04's duplicate overlap is supported; permanent omission additionally depends
  on the UI stopping rule and was not certified. That stronger consequence is
  unnecessary to establish the semantic match.
- E06's candidate also discusses scan/iteration handling. Agreement rests on the
  shared query-failure path, not equivalence of every additional assertion.
- E07 requires failed loading, successful fallback, and training enabled.
- No divergent training or deployment scenario was executed.

## Unmatched findings and B/H comparison

The published inputs contain 4, 4, and 7 global candidates for p06, p08, and p17.
Every candidate has a nonempty correspondence, and each task's
`classifications.json` is `[]`. Consequently, there is **no unmatched H candidate**
in these inputs to adjudicate.

Within the sampled mechanisms, E02 and E06 have B-only reference coverage;
E03, E05, and E07 have H-only coverage. These are differences in emitted reviews,
not evidence that the matching judge rejected an H finding. Treating an absent
reviewer finding as a judge false negative would conflate two failure modes.
Other reference misses were not independently assessed.

## Gate implications and limits

The sample contains only accepted positive matches. It cannot measure
false-positive discrimination, erroneous rejection, unsampled recall, stability
across runs, or sensitivity to wording. Eight agreements must not be presented as
100% benchmark accuracy or proof of genuine quality/cost improvement.

Use the present judgments as provisional evidence, not sole gate certification.
A stronger gate needs an independently specified audit covering negative and
ambiguous decisions, including FP-labelled references, without selecting
favorable labels or tuning reviewer output to known reference content.
That broader work was not performed here.

This audit used only allowed judgment artifacts and HEAD/BASE Git objects.
No hidden reasoning, network access, paid model call, application/test execution,
review rerun, rescore, or source/test/prompt change was performed. It makes no
claim about the concurrent E9 pilot. Only this report and its JSON companion
were intentionally written.
