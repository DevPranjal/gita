"""Tiny stdlib HTTP server for the gitpp vs git comparison UI.

Run with::

    python web/app.py            # http://127.0.0.1:8765

No external deps. Serves ``index.html`` and exposes:

  GET /api/scenarios            -> list of scenarios + their git_conflicts.md
  GET /api/merge?name=<scen>    -> {base, ours, theirs, expected,
                                     git_output, git_conflicted,
                                     gitpp_output, gitpp_conflicts}
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import libcst as cst

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "tests" / "scenarios"
WEB_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT / "src"))
from gitpp.manifest import build_manifest, diff_sources, render_manifest  # noqa: E402
from gitpp.merge import merge_modules  # noqa: E402
from gitpp.repo import Repo  # noqa: E402


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _list_scenarios() -> list[dict]:
    out = []
    for d in sorted(SCENARIOS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "base.py").exists():
            continue
        explainer = (d / "git_conflicts.md")
        out.append(
            {
                "name": d.name,
                "explainer": _read(explainer) if explainer.exists() else "",
            }
        )
    return out


def _git_merge(base: Path, ours: Path, theirs: Path) -> tuple[str, bool]:
    """Run ``git merge-file -p``. Returns (output, had_conflict)."""
    r = subprocess.run(
        ["git", "merge-file", "-p", str(ours), str(base), str(theirs)],
        capture_output=True,
        text=True,
    )
    # git merge-file returns 0 = clean, >0 = N conflicts, <0 = error
    return r.stdout, r.returncode != 0


def _gitpp_merge(base: str, ours: str, theirs: str) -> tuple[str, list[dict]]:
    base_m = cst.parse_module(base)
    ours_m = cst.parse_module(ours)
    theirs_m = cst.parse_module(theirs)
    merged, conflicts = merge_modules(base_m, ours_m, theirs_m)
    return merged.code, [
        {"kind": c.kind, "key": list(c.key), "detail": c.detail} for c in conflicts
    ]


def _scenario_payload(name: str) -> dict:
    d = SCENARIOS_DIR / name
    if not (d / "base.py").exists():
        raise FileNotFoundError(name)
    base = _read(d / "base.py")
    ours = _read(d / "ours.py")
    theirs = _read(d / "theirs.py")
    expected = _read(d / "expected.py") if (d / "expected.py").exists() else ""

    git_out, git_conflicted = _git_merge(d / "base.py", d / "ours.py", d / "theirs.py")
    try:
        gpp_out, gpp_conflicts = _gitpp_merge(base, ours, theirs)
        gpp_error = None
    except Exception as exc:  # pragma: no cover - surface to UI
        gpp_out, gpp_conflicts, gpp_error = "", [], f"{type(exc).__name__}: {exc}"

    return {
        "name": name,
        "base": base,
        "ours": ours,
        "theirs": theirs,
        "expected": expected,
        "git_output": git_out,
        "git_conflicted": git_conflicted,
        "gitpp_output": gpp_out,
        "gitpp_conflicts": gpp_conflicts,
        "gitpp_error": gpp_error,
        "matches_expected": bool(expected) and gpp_out == expected,
    }


def _git_diff_text(a: Path, b: Path) -> str:
    """Run ``git diff --no-index`` for a textual unified diff. Empty if identical."""
    r = subprocess.run(
        ["git", "--no-pager", "diff", "--no-index", "--no-color", str(a), str(b)],
        capture_output=True,
        text=True,
    )
    # git diff --no-index returns 1 when files differ; that's normal.
    return r.stdout


def _diff_pair(base_src: str, side_src: str, label: str) -> dict:
    """Build {manifest, rendered, git_diff, git_lines} comparing base→side."""
    entry = diff_sources(base_src, side_src, path=label)
    manifest = build_manifest([entry], from_sha=None, to_sha=None)
    return {
        "manifest": manifest,
        "rendered": render_manifest(manifest),
    }


def _diff_payload(name: str) -> dict:
    """Per-scenario manifests for base→ours and base→theirs, plus git diff sizes."""
    d = SCENARIOS_DIR / name
    if not (d / "base.py").exists():
        raise FileNotFoundError(name)
    base = _read(d / "base.py")
    ours = _read(d / "ours.py")
    theirs = _read(d / "theirs.py")

    git_ours = _git_diff_text(d / "base.py", d / "ours.py")
    git_theirs = _git_diff_text(d / "base.py", d / "theirs.py")

    ours_pair = _diff_pair(base, ours, "users.py")
    theirs_pair = _diff_pair(base, theirs, "users.py")

    return {
        "name": name,
        "ours": {
            **ours_pair,
            "git_diff": git_ours,
            "git_lines": git_ours.count("\n"),
        },
        "theirs": {
            **theirs_pair,
            "git_diff": git_theirs,
            "git_lines": git_theirs.count("\n"),
        },
    }


# Spotlight symbol per scenario — drives the symbol-log / callers demo.
_PILLAR_SYMBOL = {
    "rename-vs-edit": "fetch_user",
    "parallel-methods": "Service",
    "import-reorder-add": "main",
}


def _pillars_payload(name: str) -> dict:
    """Run all three pillars end-to-end in a tempdir and return a single bundle.

    Recording-friendly: one HTTP call gives the UI everything it needs to render
    the three-pillar demo for a chosen scenario.
    """
    d = SCENARIOS_DIR / name
    if not (d / "base.py").exists():
        raise FileNotFoundError(name)

    base_src = _read(d / "base.py")
    change_src = _read(d / "ours.py")
    git_diff = _clean_git_diff(_git_diff_text(d / "base.py", d / "ours.py"))

    # Build the agent-facing diff (Pillar 1) without committing.
    pillar1_manifest = build_manifest(
        [diff_sources(base_src, change_src, path="users.py")],
        from_sha=None,
        to_sha=None,
    )

    # Run the full commit cycle in a tempdir for Pillars 2 and 3.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo = Repo.init(tmp_path)
        f = tmp_path / "users.py"
        f.write_text(base_src, encoding="utf-8")
        repo.add(f)
        initial_sha = repo.commit("initial users module")

        f.write_text(change_src, encoding="utf-8")
        repo.add(f)
        change_sha = repo.commit(_change_message(name))

        explain_manifest = repo.read_manifest(change_sha)
        symbol = _PILLAR_SYMBOL.get(name, "")
        symbol_log = repo.symbol_log(symbol) if symbol else []
        callers = repo.find_callers(symbol) if symbol else []

    return {
        "scenario": name,
        "symbol": symbol,
        "pillar1": {
            "git_diff": git_diff,
            "git_diff_lines": git_diff.count("\n"),
            "manifest": pillar1_manifest,
            "manifest_rendered": render_manifest(pillar1_manifest),
            "op_count": _op_count(pillar1_manifest),
        },
        "pillar2": {
            "commit_sha": change_sha,
            "initial_sha": initial_sha,
            "commit_message": _change_message(name),
            "explain_rendered": render_manifest(explain_manifest) if explain_manifest else "",
        },
        "pillar3": {
            "symbol_log": symbol_log,
            "callers": callers,
        },
    }


def _change_message(name: str) -> str:
    """Human-readable commit message per scenario."""
    return {
        "rename-vs-edit": "rename get_user to fetch_user",
        "parallel-methods": "add Service.decommission",
        "import-reorder-add": "add logging import",
    }.get(name, "apply change")


def _clean_git_diff(text: str) -> str:
    """Strip diff/index/---/+++ headers so the body is readable in the UI."""
    skip_prefixes = ("diff --git", "index ", "--- ", "+++ ")
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith(skip_prefixes)
    )


def _op_count(manifest: dict) -> int:
    s = manifest.get("summary", {})
    return s.get("logic_ops", 0) + s.get("signature_ops", 0) + s.get("cosmetic_ops", 0)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        url = urlparse(self.path)
        if url.path in ("/", "/index.html", "/pillars", "/pillars.html"):
            self._send_file(WEB_DIR / "index.html", "text/html")
            return
        if url.path == "/api/scenarios":
            self._send_json(200, _list_scenarios())
            return
        if url.path == "/api/merge":
            q = parse_qs(url.query)
            name = (q.get("name") or [""])[0]
            try:
                self._send_json(200, _scenario_payload(name))
            except FileNotFoundError:
                self._send_json(404, {"error": f"unknown scenario: {name}"})
            return
        if url.path == "/api/diff":
            q = parse_qs(url.query)
            name = (q.get("name") or [""])[0]
            try:
                self._send_json(200, _diff_payload(name))
            except FileNotFoundError:
                self._send_json(404, {"error": f"unknown scenario: {name}"})
            return
        if url.path == "/api/pillars":
            q = parse_qs(url.query)
            name = (q.get("name") or ["rename-vs-edit"])[0]
            try:
                self._send_json(200, _pillars_payload(name))
            except FileNotFoundError:
                self._send_json(404, {"error": f"unknown scenario: {name}"})
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:  # quieter
        sys.stderr.write(f"[gita-web] {fmt % args}\n")


def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"gita: http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
