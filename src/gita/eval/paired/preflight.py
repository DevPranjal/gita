from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import read_json, utc_now, write_json
from .dataset import resolve_scope


def check(task: dict, root: Path) -> dict:
    try:
        scope = resolve_scope(task, root / task["id"])
        return {"task": task["id"], "repo": task["repo"], "status": "available", **scope}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return {"task": task["id"], "repo": task["repo"],
                "status": "unavailable", "error": str(error)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repos", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check, t, args.repos) for t in manifest["tasks"]]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) % 20 == 0:
                print(f"Resolved {len(results)}/{len(futures)} frozen pairs", flush=True)
    report = {"at": utc_now(), "manifest_digest": manifest["manifest_digest"],
              "tasks": len(results), "available": sum(r["status"] == "available" for r in results),
              "adjusted": sum(r.get("base_adjusted", False) for r in results),
              "results": sorted(results, key=lambda r: r["task"])}
    write_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "results"}), flush=True)


if __name__ == "__main__":
    main()
