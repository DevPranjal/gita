"""Authored standard-library probes for external contracts used by quality judges."""
from __future__ import annotations

import json
import os
import re
import subprocess

from .common import ProtocolError, digest, require


NODE_CALLBACK_PROBE = """const dc = require('node:diagnostics_channel');
async function run(name, fail, asynchronous) {
  const tracing = dc.tracingChannel('gita.authored.contract.' + name);
  let errors = 0, callbacks = 0;
  const observe = context => {
    if (context.error?.message === 'authored failure') errors++;
  };
  tracing.error.subscribe(observe);
  try {
    await new Promise(resolve => {
      tracing.traceCallback(callback => {
        const invoke = () => callback(fail ? new Error('authored failure') : null);
        if (asynchronous) setImmediate(invoke); else invoke();
      }, 0, {}, null, () => { callbacks++; resolve(); });
    });
    return {name, errors, callbacks};
  } finally {
    tracing.error.unsubscribe(observe);
  }
}
(async () => {
  const cases = [await run('sync_error', true, false),
                 await run('async_error', true, true),
                 await run('success', false, false)];
  console.log(JSON.stringify({node: process.version, cases}));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
"""


def validate_contracts(contracts: dict) -> None:
    require(type(contracts) is dict and set(contracts) == {"node_trace_callback"},
            "Unsupported judge runtime contract")
    contract = contracts["node_trace_callback"]
    require(type(contract) is dict and set(contract) == {"probe", "probe_digest", "observed"},
            "Malformed judge runtime contract")
    require(contract["probe"] == NODE_CALLBACK_PROBE
            and contract["probe_digest"] == digest(NODE_CALLBACK_PROBE),
            "Judge runtime probe changed")
    observed = contract["observed"]
    require(type(observed) is dict and set(observed) == {"node", "cases"}
            and type(observed["node"]) is str
            and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", observed["node"]) is not None,
            "Missing exact Node runtime version")
    expected = [
        {"name": "sync_error", "errors": 1, "callbacks": 1},
        {"name": "async_error", "errors": 1, "callbacks": 1},
        {"name": "success", "errors": 0, "callbacks": 1},
    ]
    require(observed["cases"] == expected
            and all(type(row.get(field)) is int
                    for row in observed["cases"] for field in ("errors", "callbacks")),
            "Node callback contract qualification failed")


def prepare_contracts() -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("NODE_OPTIONS", "NODE_PATH")}
    try:
        result = subprocess.run(
            ["node", "--input-type=commonjs", "-e", NODE_CALLBACK_PROBE],
            check=True, capture_output=True, text=True, timeout=15, env=env)
        observed = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ProtocolError("Cannot qualify the authored Node callback contract") from error
    contracts = {"node_trace_callback": {
        "probe": NODE_CALLBACK_PROBE, "probe_digest": digest(NODE_CALLBACK_PROBE),
        "observed": observed}}
    validate_contracts(contracts)
    return contracts


def supplement_context(context: dict, contracts: dict) -> dict:
    validate_contracts(contracts)
    if "traceCallback" not in json.dumps(context, ensure_ascii=True):
        return context
    node = contracts["node_trace_callback"]
    return {**context, "verified_runtime_evidence": {
        "node_trace_callback": node,
        "official_implementation_reference": (
            "https://github.com/nodejs/node/blob/"
            + node["observed"]["node"] + "/lib/diagnostics_channel.js"),
        "scope": "Authored Node built-in-only probe; no benchmark program or tests executed.",
        "limits": ("Observed on the recorded runtime only. Inspect supplied feature/version gates; "
                   "do not infer behavior on untested runtimes or assume a resolved caller graph.")}}
