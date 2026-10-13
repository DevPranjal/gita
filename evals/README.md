# Evaluation harness

The optional paired harness compares agent code reviews with and without gita.
It records native usage, isolates evidence tools, grades reference-issue
agreement blind to the arm, and retains failed attempts rather than treating
them as free successes.

The release tree includes the harness, its tests, the
[frozen input manifest](aacr-manifest.json), [historical protocol](PROTOCOL.md)
and one [historical results summary](RESULTS.md). Generated reports, traces,
evidence bundles, local investigation logs and third-party checkouts are not
distributed. The historical results are not a benchmark of the current release,
and cannot be replayed from the summary alone.

## Prerequisites

- Python 3.11+, Git, authenticated GitHub CLI (`gh`).
- Install from the repository root with
  `python -m pip install -c constraints.txt -e ".[dev]"` and provision grammars
  as described in the [quickstart](../docs/QUICKSTART.md).
- Authenticated Copilot CLI with support for `gpt-5.6-sol`, JSON output,
  native usage export and tool allowlists. The historical study used CLI
  `1.0.86-2`; availability and behavior of later versions may differ.

Model calls consume credits and send benchmark evidence to the configured model
provider. Use only repositories you are authorized to process that way. Product
CLI, MCP and web-demo operation does not require these model calls.

The input is the public Apache-2.0
[AACR-Bench snapshot](https://github.com/alibaba/aacr-bench/tree/68a569759289a83654a59d06db2a72910edf0a4a).
It contains 196 PR cases with ten language labels. Reference agreement is not
executable bug-fixing ground truth.

## Run a new experiment

Run from the repository root. On Linux/macOS use `.venv/bin/python`. All
generated artifacts go under ignored `evals/runs/`, not beside the release docs.

```powershell
$python = ".\.venv\Scripts\python.exe"
& $python -m pytest tests\test_paired_eval.py tests\test_eval.py tests\test_mcp.py -q

& $python -m gita.eval.paired.dataset `
  --download --source evals\runs\sources\positive_samples.json `
  --manifest evals\aacr-manifest.json

& $python -m gita.eval.paired.preflight `
  --manifest evals\aacr-manifest.json --repos evals\runs\repos `
  --output evals\runs\preflight.json

& $python -m gita.eval.paired.runner `
  --manifest evals\aacr-manifest.json --repos evals\runs\repos `
  --runs evals\runs\my-pilot --split pilot --workers 2

& $python -m gita.eval.paired.reconcile --runs evals\runs\my-pilot

& $python -m gita.eval.paired.judge `
  --manifest evals\aacr-manifest.json `
  --source evals\runs\sources\positive_samples.json `
  --runs evals\runs\my-pilot --output evals\runs\my-pilot-grades --reconciled

& $python -m gita.eval.paired.report `
  --manifest evals\aacr-manifest.json --runs evals\runs\my-pilot `
  --grades evals\runs\my-pilot-grades --output evals\runs\my-pilot.md --reconciled
```

Choose a new run directory for each experiment. The runner records the product
commit, dependency versions, prompt and harness digests, and refuses mixed
protocols or overwritten attempts. Use `--help` on individual modules for
monitoring, export, verification and sensitivity-analysis options.

After validating instrumentation, freeze a study plan before running
`--split confirmation`. Keep pilot, gate and holdout cohorts separate. Do not
select tasks, repeat calls or stop based on favorable results.

## Interpretation and artifact handling

Native usage, response-format validity and usable review content are separate.
Missing metrics are not zero; tool-output tokens are diagnostic, not billed
model tokens. Report quality, cost, cache behavior and failures together.
The [protocol](PROTOCOL.md) documents the historical controls and limitations.

Retain raw artifacts privately for your own verification. Review tool arguments,
findings and logs for sensitive content before sharing; export checks are not a
complete privacy audit. Never commit credentials or raw CLI journals. No model
evaluation needs to run for the ordinary product test suite.

The legacy `gita.eval.main`, keyword-scoring helpers and offline dashboard remain
for compatibility. Legacy runs require your own task YAML and corpus; those
inputs are not bundled. Use the paired harness above for new review studies.
