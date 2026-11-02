"""Combine actual public benchmark receipts into compact, path-sanitized evidence."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


def sanitize(value):
    """Remove path-shaped local detail in errors; never alter numerical evidence."""
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        urls = []

        def protect(match):
            urls.append(match.group())
            return f"__PUBLIC_URL_{len(urls) - 1}__"

        protected = re.sub(r"\bhttps?://[^\s\"'<>]+", protect, value)
        protected = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>]+", "<local-path-redacted>", protected)
        protected = re.sub(r"(?<![\w./:])/(?!/)[^\s\"'<>]+", "<local-path-redacted>", protected)
        for index, url in enumerate(urls):
            protected = protected.replace(f"__PUBLIC_URL_{index}__", url)
        return protected
    return value


def export(runs, golden_receipts, output, initial_hashes=None, diagnostic_receipts=()):
    campaigns = []
    for number, root in enumerate(runs, 1):
        result = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        result["campaign"] = number
        if number == 1 and "tooling_sha256" not in result:
            result["tooling_sha256"] = initial_hashes or {}
            result["tooling_hash_provenance"] = "observed during initial campaign; initial supervisor did not emit hashes"
        campaigns.append(result)
    data = {
        "schema_version": 1, "campaigns": campaigns,
        "untimed_golden_attempts": [
            json.loads(path.read_text(encoding="utf-8")) for path in golden_receipts
        ],
        "untimed_diagnostics": [
            json.loads(path.read_text(encoding="utf-8")) for path in diagnostic_receipts
        ],
        "final_tooling_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("bench_public_index.py", "public_index_worker.py", "verify_public_spans.py",
                         "export_public_index.py", "index_oracle.py")
        },
        "interpretation": {
            "baseline_comparison": "not performed",
            "completed_seconds": "build-only time gated on successful publication and all stated checks",
            "resources": "sampled over the entire child including verification, not builder alone",
            "lineage_truth": "not independently established for real repositories",
            "coverage": "partial publications retained, not equated with complete extraction/matching",
            "initial_flask_failure": "verification oracle included decorators; indexed def/class spans intentionally exclude wrappers",
            "initial_golden_failure": "verification query used nonexistent scope.generation_id; corrected to published_generation_id",
            "non_python_oracle": "two hand-reviewed pinned snapshot spans only",
        },
    }
    data = sanitize(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--golden-receipt", action="append", type=Path, default=[])
    parser.add_argument("--initial-hashes", type=Path)
    parser.add_argument("--diagnostic-receipt", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    initial = json.loads(args.initial_hashes.read_text()) if args.initial_hashes else None
    export(args.run, args.golden_receipt, args.output, initial, args.diagnostic_receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
