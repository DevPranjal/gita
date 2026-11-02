from __future__ import annotations

import argparse
import json
from pathlib import Path

from .campaign import pair_digest, run, seal
from .common import ProtocolError, read_json, write_json
from .dataset import freeze, public_manifest, verify
from .judge import grade
from .preparation import policy_digest, preflight, prepare, provision_parsers
from .report import report


def main() -> int:
    parser = argparse.ArgumentParser(description="Pinned indexed ReviewBench auto experiment (no legacy).")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("policy-digest")
    for name in ("freeze", "prepare", "seal", "grade"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        if name in ("freeze", "grade"):
            command.add_argument("--grader", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--manifest-export", type=Path)
        if name == "prepare":
            command.add_argument("--index", action="store_true")
            command.add_argument("--policy-ready")
            command.add_argument("--task-limit", type=int, choices=(1, 25), default=25)
        if name in ("seal", "grade"):
            command.add_argument("--run-root", type=Path, required=True)
        if name == "seal":
            command.add_argument("--seed", type=int, default=20261014)
    execute = sub.add_parser("run")
    execute.add_argument("--run-root", type=Path, required=True)
    execute.add_argument("--task-limit", type=int, choices=(1, 25), required=True)
    execute.add_argument("--audit-approval", required=True)
    execute.add_argument("--pilot-approval")
    for name in ("report", "pair-digest"):
        command = sub.add_parser(name)
        command.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "policy-digest":
            result = {"product_policy_digest": policy_digest()}
        elif args.operation == "freeze":
            result = freeze(args.root, args.grader)
            if args.manifest_export:
                write_json(args.manifest_export, public_manifest(args.root))
            result = {"pin": result["pin"], "digest": result["digest"],
                      "tasks": result["full_count"], "starter_tasks": result["starter_count"]}
        elif args.operation == "prepare":
            frozen = verify(args.root)
            tasks = {t["id"]: t for t in frozen["tasks"]}
            parsers = provision_parsers()
            write_json(args.root / "parsers.json", parsers)
            scopes = [prepare(args.root, tasks[key], index=args.index, ready_policy=args.policy_ready)
                      for key in frozen["starter_ids"][:args.task_limit]]
            write_json(args.root / "prepared-scopes.json", scopes)
            result = [{"task": s["task"]["id"], "index": s["index_preparation"],
                       "preflight": preflight(s)} for s in scopes]
        elif args.operation == "seal":
            result = seal(args.root, args.run_root, read_json(args.root / "prepared-scopes.json"),
                          read_json(args.root / "parsers.json"), seed=args.seed)
            result = {"protocol": result["digest"], "scheduled_slots": 50,
                      "status": "Independent audit REQUIRED before run"}
        elif args.operation == "run":
            result = run(args.run_root, task_limit=args.task_limit, audit_approval=args.audit_approval,
                         pilot_approval=args.pilot_approval)
        elif args.operation == "grade":
            result = grade(args.root, args.run_root, args.grader)
        elif args.operation == "pair-digest":
            result = {"initial_pair_receipt_digest": pair_digest(args.run_root)}
        else:
            result = report(args.run_root)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        if args.operation == "run" and result.get("stop_reason") is not None:
            return 1
        if args.operation == "grade" and result.get("pending"):
            return 1
        return 0
    except (ProtocolError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "refused", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
