"""Run a loopback-only repository viewer: python -m gita.web."""

from __future__ import annotations

import argparse
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from tempfile import TemporaryFile
from threading import BoundedSemaphore
from urllib.parse import parse_qs, urlsplit

from gita.mcp.tools import (
    diff_tool, entities_tool, history_tool, id_tool, index_tool, show_tool, source_tool,
)
from gita.web.demo import create_demo, git


ASSETS = Path(__file__).parent
SCOPE = "relay"
BUDGET = 60000
CLI_SLOTS = BoundedSemaphore(2)


class DemoView:
    def __init__(self, repo: Path, scope: str = SCOPE):
        self.repo = repo.resolve()
        self.scope = scope
        self.commits = []
        for record in git(self.repo, "log", "--first-parent", "--format=%H%x00%s%x00%an%x00%as").splitlines():
            sha, subject, author, date = record.split("\0")
            self.commits.append({"sha": sha, "subject": subject, "author": author, "date": date})
        if not self.commits:
            raise ValueError("The repository needs at least one committed revision.")
        self.head = self.commits[0]["sha"]
        self.revisions = {commit["sha"] for commit in self.commits}
        self.tags = []
        for name in git(self.repo, "tag", "--list").splitlines():
            sha = git(self.repo, "rev-parse", "--verify", f"refs/tags/{name}^{{commit}}")
            if sha in self.revisions:
                self.tags.append({"name": name, "sha": sha})
        self.status = index_tool(str(self.repo), scope=self.scope)
        if self.status.get("error"):
            raise ValueError(f"Index unavailable: {self.status['error']}")
        self.generation = self.status["generation"]

    def revision(self, value: str | None) -> str:
        value = value or self.head
        if value not in self.revisions:
            raise ValueError("Select a commit from this repository's history.")
        return value

    @lru_cache(maxsize=32)
    def catalog(self, at: str) -> dict:
        paths = git(self.repo, "ls-tree", "-r", "--name-only", "-z", at).split("\0")
        entities = []
        files = []
        for path in filter(None, paths):
            page = entities_tool(str(self.repo), path, at=at, scope=self.scope,
                                 generation=self.generation, limit=100, budget=BUDGET)
            if page.get("error"):
                files.append({"path": path, "status": page.get("status", "unknown"), "count": 0})
                continue
            occurrences = list(page["results"])
            while page.get("page", {}).get("next_cursor"):
                page = entities_tool(str(self.repo), path, at=at, scope=self.scope,
                                     generation=self.generation, limit=100, budget=BUDGET,
                                     cursor=page["page"]["next_cursor"])
                if page.get("error"):
                    raise ValueError(f"Could not complete entity page for {path}: {page['error']}")
                occurrences.extend(page["results"])
            named = [item for item in occurrences if item["names"] != ["<module>"]]
            files.append({"path": path, "status": page["status"], "count": len(named)})
            for occurrence in named:
                entities.append({"id": path + "::" + "::".join(occurrence["names"]),
                                 "name": "::".join(occurrence["names"]), "path": path,
                                 "line": occurrence["span"]["start_line"]})
        return {"entities": entities, "files": files, "coverage": self.status["coverage"]}

    def bootstrap(self) -> dict:
        default_base = next((tag["sha"] for tag in self.tags if tag["name"] == "v0.2.0"),
                            self.commits[min(1, len(self.commits) - 1)]["sha"])
        return {"name": self.repo.name, "commits": self.commits, "tags": self.tags,
                "head": self.head, "base": default_base, "scope": self.scope,
                "generation": self.generation, **self.catalog(self.head)}

    @lru_cache(maxsize=64)
    def compare(self, base: str, head: str) -> dict:
        return diff_tool(str(self.repo), base=base, head=head, budget=12000)

    @lru_cache(maxsize=128)
    def patch(self, entity: str, base: str, head: str) -> dict:
        return show_tool(str(self.repo), entity=entity, base=base, head=head)

    @lru_cache(maxsize=128)
    def entity(self, entity: str, at: str) -> dict:
        identity = id_tool(str(self.repo), entity, at=at, scope=self.scope,
                           generation=self.generation, historical=True, budget=BUDGET)
        if identity.get("error"):
            return identity
        matches = identity.get("results", [])
        if len(matches) != 1:
            return {"error": {"message": "This name has multiple identities. Select a more specific entity."}}
        match = matches[0]
        occurrence = match.get("occurrence") or match.get("last_known_occurrence")
        history = history_tool(str(self.repo), entity=match["eid"], at=at, indexed=True,
                               scope=self.scope, generation=self.generation, limit=100, budget=BUDGET)
        source = source_tool(str(self.repo), occurrence, scope=self.scope, budget=BUDGET) if occurrence else None
        return {"identity": match, "history": history, "source": source,
                "coverage": identity["coverage"], "generation": self.generation}

    def api(self, route: str, params: dict[str, list[str]]) -> dict:
        def argument(name: str, default: str = "") -> str:
            values = params.get(name, [default])
            if len(values) != 1 or len(values[0]) > 2048:
                raise ValueError(f"Invalid {name} parameter.")
            return values[0]

        if route == "/api/bootstrap":
            return self.bootstrap()
        if route == "/api/catalog":
            return self.catalog(self.revision(argument("at")))
        if route == "/api/diff":
            return self.compare(self.revision(argument("base")), self.revision(argument("head")))
        if route in {"/api/patch", "/api/entity"}:
            entity = argument("entity")
            if not entity or "\0" in entity:
                raise ValueError("Select a named entity.")
            if route == "/api/patch":
                return self.patch(entity, self.revision(argument("base")), self.revision(argument("head")))
            return self.entity(entity, self.revision(argument("at")))
        raise FileNotFoundError("Unknown API route.")

    def cli_revision(self, value: str) -> str:
        if value == "HEAD":
            return self.head
        relative = re.fullmatch(r"HEAD(?:~(\d{1,3})|(\^+))", value)
        if relative:
            offset = int(relative[1]) if relative[1] is not None else len(relative[2])
            if offset < len(self.commits):
                return self.commits[offset]["sha"]
        for tag in self.tags:
            if value == tag["name"]:
                return tag["sha"]
        matches = [sha for sha in self.revisions if sha.startswith(value)]
        if re.fullmatch(r"[0-9a-f]{7,64}", value) and len(matches) == 1:
            return matches[0]
        raise ValueError("Revision is outside the published repository history.")

    def cli(self, command: str) -> dict:
        from gita.cli import _parser

        if not isinstance(command, str) or not command or len(command) > 2048:
            raise ValueError("Enter a Gita command of at most 2048 characters.")
        if any(character in command for character in ("\n", "\r", "\0", ";", "|", "&", "`", "$")):
            raise ValueError("Shell operators are not supported.")
        tokens = shlex.split(command)
        if not tokens or tokens.pop(0) != "gita":
            raise ValueError("Commands must start with gita.")
        parser = _parser(indexed_errors=True)
        if not tokens or tokens == ["--help"]:
            return {"output": "Read-only Gita CLI\n\nCommands: diff, show, expand, history, id, index --status\n\n" 
                    "gita diff v0.2.0 HEAD\ngita history calculate_order_total --indexed\ngita id shipping_cost\n",
                    "exit_code": 0, "truncated": False}
        verb = tokens[0]
        if verb not in {"diff", "show", "expand", "history", "id", "index"}:
            raise ValueError("Only diff, show, expand, history, id and index --status are available.")
        if tokens[1:] in (["--help"], ["-h"]):
            return {"output": parser.subcommands[verb].format_help(), "exit_code": 0, "truncated": False}
        switches = {"--json", "--no-color", "--brief", "--interface-only", "--patch", "--indexed", "--historical", "--status"}
        values = {"--filter", "--intent", "--budget", "--limit", "--order", "--at", "--since", "--until"}
        positional_tokens = []
        remaining = iter(tokens[1:])
        for token in remaining:
            if not token.startswith("-"):
                positional_tokens.append(token)
                continue
            flag, separator, value = token.partition("=")
            if flag in values:
                if not separator and next(remaining, None) is None:
                    raise ValueError(f"Missing value for {flag}.")
            elif flag not in switches or separator:
                raise ValueError(f"Option {flag} is not available in the public CLI.")
        args = parser.parse_args(tokens)
        if not 0 <= args.budget <= 12000 or not 1 <= getattr(args, "limit", 20) <= 100:
            raise ValueError("Use a budget of 0-12000 tokens and a limit of 1-100.")
        options = ["--no-color"]
        if args.as_json:
            options.append("--json")
        positionals = []
        if verb in {"diff", "show", "expand"}:
            if verb != "diff":
                positionals.append(args.entity)
            base = self.cli_revision(args.base)
            head = self.cli_revision(args.head or "HEAD")
            if verb == "diff" and not positional_tokens:
                base = self.commits[min(1, len(self.commits) - 1)]["sha"]
            positionals.extend([base, head])
        elif verb == "history":
            options.extend(["--indexed", "--scope", self.scope, "--generation", self.generation])
            if args.entity and args.base:
                if args.head or args.since or args.until or args.at:
                    raise ValueError("Select an entity or exactly two revisions.")
                options.extend(["--since", self.cli_revision(args.entity), "--until", self.cli_revision(args.base)])
            elif args.entity:
                if args.since or args.until:
                    raise ValueError("Entity history uses --at, not --since or --until.")
                positionals.append(args.entity)
                options.extend(["--at", self.cli_revision(args.at or "HEAD")])
            elif args.since and args.until and not args.at:
                options.extend(["--since", self.cli_revision(args.since), "--until", self.cli_revision(args.until)])
            else:
                raise ValueError("Select an entity or two revisions for history.")
        elif verb == "id":
            positionals.append(args.entity)
            options.extend(["--scope", self.scope, "--generation", self.generation,
                            "--at", self.cli_revision(args.at or "HEAD")])
        elif not args.status:
            raise ValueError("Index writes are disabled. Use gita index --status.")
        else:
            options.extend(["--status", "--scope", self.scope])
        for name in ("brief", "interface_only", "patch", "historical"):
            if getattr(args, name, False):
                options.append("--" + name.replace("_", "-"))
        for name in ("filter", "intent", "limit", "order"):
            value = getattr(args, name, None)
            if value not in (None, ""):
                options.append("--" + name + "=" + str(value))
        if verb != "show":
            options.append(f"--budget={args.budget}")
        if verb == "diff" and args.indexed:
            options.extend(["--indexed", "--scope", self.scope, "--generation", self.generation])
        arguments = [sys.executable, "-m", "gita", "-C", str(self.repo), verb, *options]
        if positionals:
            arguments.extend(["--", *positionals])
        if not CLI_SLOTS.acquire(blocking=False):
            raise ValueError("CLI is busy. Try again shortly.")
        try:
            env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LOCALAPPDATA", "USERPROFILE") if name in os.environ}
            env.update(PYTHONPATH=str(ASSETS.parents[1]), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
            with TemporaryFile() as output:
                try:
                    result = subprocess.run(arguments, cwd=self.repo, env=env, stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=subprocess.STDOUT, timeout=25)
                except subprocess.TimeoutExpired:
                    return {"output": "Command exceeded the 25-second limit.", "exit_code": 124, "truncated": False}
                output.seek(0)
                content = output.read(128001)
            return {"output": content[:128000].decode("utf-8", errors="replace"),
                    "exit_code": result.returncode, "truncated": len(content) > 128000}
        finally:
            CLI_SLOTS.release()


def make_server(view: DemoView, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(35)

        def log_message(self, format: str, *args) -> None:
            pass

        def respond(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed_hosts:
                self.respond(403, b'{"error":"Local requests only."}', "application/json")
                return
            url = urlsplit(self.path)
            try:
                if url.path.startswith("/api/"):
                    result = view.api(url.path, parse_qs(url.query, keep_blank_values=True, max_num_fields=10))
                    self.respond(200, json.dumps(result).encode("utf-8"), "application/json; charset=utf-8")
                    return
                assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                          "/style.css": ("style.css", "text/css"), "/lucide.min.js": ("lucide.min.js", "text/javascript")}
                if url.path not in assets:
                    raise FileNotFoundError("Not found.")
                filename, content_type = assets[url.path]
                self.respond(200, (ASSETS / filename).read_bytes(), content_type + "; charset=utf-8")
            except FileNotFoundError as error:
                self.respond(404, json.dumps({"error": str(error)}).encode(), "application/json")
            except ValueError as error:
                self.respond(400, json.dumps({"error": str(error)}).encode(), "application/json")
            except Exception:
                self.respond(500, b'{"error":"The local query failed. Check the repository and index."}', "application/json")

        def do_POST(self) -> None:
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in hosts:
                self.respond(403, b'{"error":"Local requests only."}', "application/json")
                return
            if self.path != "/api/cli":
                self.respond(405, b'{"error":"Read-only endpoint."}', "application/json")
                return
            if self.headers.get("Sec-Fetch-Site", "same-origin") not in {"same-origin", "none"}:
                self.respond(403, b'{"error":"Same-origin requests only."}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected a JSON command body, at most 8192 bytes.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {"command"}:
                    raise ValueError("Expected a command field.")
                result = view.cli(payload["command"])
                self.respond(200, json.dumps(result).encode("utf-8"), "application/json; charset=utf-8")
            except (ValueError, TypeError) as error:
                self.respond(400, json.dumps({"error": str(error)}).encode(), "application/json")
            except Exception:
                self.respond(500, b'{"error":"Command execution failed."}', "application/json")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(".demo/relay"))
    parser.add_argument("--scope", default=SCOPE)
    parser.add_argument("--port", type=int, default=7357)
    parser.add_argument("--create-demo", action="store_true", help="Create the sample repo at --repo; refuses existing paths")
    parser.add_argument("--build-index", action="store_true", help="Explicitly build or update this repository's named index")
    args = parser.parse_args()
    try:
        if args.create_demo:
            create_demo(args.repo)
        if args.create_demo or args.build_index:
            print("Building the repository index...", flush=True)
            result = index_tool(str(args.repo), action="build", scope=args.scope, budget=BUDGET)
            if result.get("error"):
                raise ValueError(str(result["error"]))
        view = DemoView(args.repo, args.scope)
        server = make_server(view, args.port)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"gita web: {error}\n")
    print(f"Gita: http://127.0.0.1:{server.server_port} (read-only, {view.repo})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()