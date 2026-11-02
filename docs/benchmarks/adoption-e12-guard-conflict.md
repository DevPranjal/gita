# E12 guard-conflict corrigendum

**Source-first result: the supplied verifier rationale omits a reassignment.**
Failure returns `q = None`, but fallback explicitly assigns `q` again at
`src/protrider/pipeline.py:458`. Therefore “loading failed, so q remains None”
does not establish training or checkpoint overwrite.

For **a still-existing checkpoint plus a non-None fallback dimension**, training
and its nested save are blocked. The prior audit disagreement with the credited
normal failed-load overwrite consequence is retained.

**Qualification/correction to any universal reading of that audit:** existing
file plus failed load *alone* does not settle all possible execution paths. If
fallback actually leaves `q` as None and all intervening calls succeed, the guard
permits training/save. This narrow source inspection does not prove whether
those helper calls can successfully accept that state. Do not claim that saving
is impossible under every imaginable failed-load execution.

## Exact identity and inspected source

- Task: `0083e3b9d289cf65d718cf844d29bac7df57f1cd603e32f48f2be5136caba41f`
- H local candidate **0**, original global candidate **5**.
- HEAD: `b462b109b630dd1e1a9a78088b18429d2baaae9b`.
- Source: `src/protrider/pipeline.py`, windows **54–91, 430–493, 500–549**.
- [Structured adjudication](adoption-e12-guard-conflict.json) records conditions,
  truth-table conventions, prior ratings, and limits.

## State transitions

1. The caller initializes `model` and `q` to None at **442–443**.
2. An existing checkpoint invokes loading at **450–452**.
3. A caught loading failure logs a warning and returns `(None, None)` at **89–91**.
4. `if model is None` at **456** enters fallback. Crucially,
   `q, gs_result = find_latent_dim(...)` at **458–479** overwrites the earlier q.
5. A replacement model is initialized with that current q at **485–491**.
6. Initial inference at **500–502** occurs before the training guard.
7. **509–510** calculate the flag using *current* path existence and *current* q:

```text
model_was_loaded = checkpoint_path and checkpoint_path.exists() and q is not None
should_train = config.autoencoder_training and not model_was_loaded
```

## Exact enclosing if conditions

Within `_run_protrider_standard`:

| Statement | Line | Enclosing if bodies, outer to inner |
|---|---:|---|
| `train(dataset, model, criterion, ...)` | 531–532 | **512:** `if should_train` |
| `save_model(model, str(checkpoint_path), q)` | 540 | **512:** `if should_train`; **539:** `if checkpoint_path` |

Neither statement remains inside the earlier `if model is None` block.
Neither is inside the optional `if config.use_wandb` setup block. The save is
not unconditional following fallback: it shares training's enclosing guard.

Passing the guard is necessary, not sufficient: earlier initialization/inference,
optional setup, training, and post-training inference must also succeed.

## Truth table

Assume a truthy checkpoint Path, as assigned by both branches at **445–448**.
`Exists` is evaluated at **509**; `q set` means `q is not None` **after fallback**.
All rows below concern a failed load.

| Exists | q set | Training enabled | Loaded flag | Train guard | Nested save guard |
|---|---|---|---|---|---|
| yes | yes | yes | true | blocked | blocked |
| yes | yes | no | true | blocked | blocked |
| yes | no | yes | false | permitted conditionally | permitted conditionally |
| yes | no | no | false | blocked | blocked |
| no | yes | yes | false | permitted conditionally | permitted conditionally |

The third row is a **syntactic possibility, not a demonstrated successful
helper execution**. It requires actual fallback output to leave q None and the
remaining operations to accept it. The fifth row requires the previously
existing file to disappear before the guard, outside the unchanged-file scenario.

**Concrete resolution:** the verifier's claim that q simply stays None is
unsupported because **458 reassigns it**. The ordinary non-None successful
fallback path cannot reach the credited overwrite. Retain the audit disagreement;
retain uncertainty about helper-dependent None fallback paths rather than
inventing an all-path proof or rescuing the classification with a different bug.

## Ratings preserved, not silently replaced

- `adoption-e10-novel-audit.json#/decisions/5`: **disagree, high confidence**;
  unchanged, with the conditional-scope clarification above.
- `adoption-e10-audited-sensitivity.json`: explicitly labelled counterfactual
  N06 flip; unchanged and not recalculated here.
- Parent-supplied source-only verifier: **kept TP**; its old decision remains
  untouched, while its persistent-None rationale is challenged here.
- Current adjudication: **retain disagree; qualify universal reachability
  language**. No official replacement label or gate approval.

Only the identified source windows were reread. No helper implementation review
outside them, corpus execution, tests, models, network access, other PR analysis,
or new scoring was performed. Existing reports and decisions remain intact.
