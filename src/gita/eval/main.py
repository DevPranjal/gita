"""Run the evaluation: every task, in both arms, N times.

    python -m gita.eval.main --tasks evals/tasks.yaml --reps 3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .runner import ArmConfig, build_revision, prepare, release_revision, run_once
from .score import summarise_runs
from .spec import load_tasks

ARMS = (ArmConfig.git(), ArmConfig.gita())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gita-eval")
    parser.add_argument("--tasks", default="evals/tasks.yaml")
    parser.add_argument("--corpus", default="spikes/attribution/corpus")
    parser.add_argument("--out", default="evals/runs")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument(
        "--variant", action="append", default=[], metavar="NAME=REVISION",
        help="an extra gita build to run in the same sweep, e.g. before=HEAD~1. "
             "Comparing sweeps cannot separate a code change from a drifting "
             "baseline; comparing arms within one sweep can.")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        tasks = [t for t in tasks if t.id in wanted]
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Absolute: the agent runs with cwd set to the corpus repo, so relative
    # paths would land inside it -- and then be deleted by the reset between runs.
    root = (Path(args.out).expanduser().resolve() / stamp)
    root.mkdir(parents=True, exist_ok=True)
    shim_dir, gita_bin = prepare(root / "_env")

    arms = list(ARMS)
    built: list[Path] = []
    for spec in args.variant:
        if "=" not in spec:
            print(f"--variant expects NAME=REVISION, got {spec!r}", file=sys.stderr)
            return 1
        name, revision = spec.split("=", 1)
        workspace = root / "_env" / f"variant-{name}"
        print(f"building gita@{revision} as arm {name!r} ...", flush=True)
        try:
            arms.append(ArmConfig.variant(name, build_revision(revision, workspace)))
        except subprocess.CalledProcessError as error:
            print(f"could not build {revision}: {error}", file=sys.stderr)
            return 1
        built.append(workspace)

    corpus = Path(args.corpus).expanduser().resolve()
    results: list[dict] = []
    total = len(tasks) * len(arms) * args.reps
    index = 0

    for rep in range(1, args.reps + 1):
        for task in tasks:
            repo_root = corpus / task.repo
            if not (repo_root / ".git").exists():
                print(f"  skip {task.id}: {repo_root} is not a repository")
                continue
            for arm in arms:
                index += 1
                run_id = f"{task.id}__{arm.name}__r{rep}"
                print(f"[{index}/{total}] {run_id} ...", flush=True)

                result = run_once(task, arm, repo_root, root / run_id,
                                  shim_dir, gita_bin, args.model, args.timeout)
                results.append(result)

                recall = result.get("recall")
                print(f"      recall={recall if recall is None else f'{recall:.0%}'}"
                      f"  prompt={result['prompt_tokens']:,}"
                      f"  tools={result['tool_tokens']:,}"
                      f"  turns={result['turns']}"
                      f"  gita={'yes' if result['used_gita'] else 'no'}"
                      f"  {result['seconds']}s", flush=True)

                (root / "results.jsonl").open("a", encoding="utf8").write(
                    json.dumps(result) + "\n")

    report = summarise_runs(results)
    (root / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf8")

    for workspace in built:
        release_revision(workspace)

    print()
    print("=" * 66)
    for arm, stats in report["by_arm"].items():
        per_correct = stats["tokens_per_correct_answer"]
        print(f"{arm:<6} runs={stats['runs']:<3} recall={stats['mean_recall']:.0%}"
              f"  prompt/run={stats['mean_prompt_tokens']:,.0f}"
              f"  tokens/correct={per_correct if per_correct is None else f'{per_correct:,.0f}'}")
    reduction = report["reduction"]["prompt_tokens"]
    print("-" * 66)
    print(f"paired reduction (real prompt tokens): "
          f"{'n/a' if reduction is None else f'{reduction:.1%}'}")
    print(f"quality delta (gita - git recall)    : {report['quality_delta']:+.1%}")
    print(f"gita adoption rate                   : {report['adoption_rate']:.0%}")

    comparisons = report.get("arm_comparisons") or []
    if len(comparisons) > 1:
        print("-" * 66)
        print("within this sweep, so both arms met the same weather:")
        for row in comparisons:
            interval = row["interval"]
            span = ("n/a" if not interval
                    else f"[{interval[0]:+.1%}, {interval[1]:+.1%}]")
            verdict = "measured" if row["separated"] else "indistinguishable"
            print(f"  {row['baseline']:>10} -> {row['treatment']:<10}"
                  f" {row['reduction']:+7.1%}  {span:<20} {verdict}")

    print(f"\nartifacts: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
