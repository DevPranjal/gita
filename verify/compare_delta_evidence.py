"""Compare published facts across interpretation fingerprints, without merging EIDs."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gita.index.model import canonical_encode
from gita.index.store import IndexStore, _load


def facts(store):
    db = store.connection
    generation = db.execute("SELECT published_generation_id FROM scope WHERE name='default'").fetchone()[0]
    definitions = {
        row["id"]: (row["path"], row["blob_format"], row["blob_digest"], row["start_byte"],
                    row["end_byte"], row["start_line"], row["end_line"], row["kind"],
                    _load(row["names_json"]), _load(row["discriminator_json"]), row["synthetic_root"],
                    store.get_version(row["version_id"]).fields()[1:])
        for row in db.execute("SELECT definition.*, parse_result.path, parse_result.blob_format, "
                              "parse_result.blob_digest FROM definition JOIN parse_result "
                              "ON parse_result.id=definition.parse_id")
    }
    occurrences = {
        row["id"]: (row["commit_format"], row["commit_digest"], row["path"], row["blob_format"],
                    row["blob_digest"], row["object_type"], row["mode"], definitions[row["definition_id"]])
        for row in db.execute("SELECT * FROM occurrence")
    }
    seeds = {
        row["id"]: (occurrences[row["occurrence_id"]], row["reason"],
                    _load(row["fields_json"])[4:] or ())
        for row in db.execute("SELECT * FROM lineage")
    }
    links = {
        row["id"]: (occurrences[row["source"]], occurrences[row["destination"]],
                    row["status"], row["method"], row["reason"], _load(row["fields_json"])[-1])
        for row in db.execute("SELECT * FROM lineage_link")
    }
    result = {}

    def collect(name, values):
        counts = Counter(sha256(canonical_encode("cross-policy-fact", value)).hexdigest() for value in values)
        result[name] = dict(sorted(counts.items()))

    collect("versions", (version.fields()[1:] for row in db.execute("SELECT id FROM version")
                          if (version := store.get_version(row["id"])) is not None))
    collect("definitions", definitions.values())
    collect("occurrences", occurrences.values())
    collect("lineages", seeds.values())
    collect("links", links.values())
    collect("events", (
        (seeds[row["eid"]], row["commit_format"], row["commit_digest"],
         None if row["source"] is None else occurrences[row["source"]],
         None if row["destination"] is None else occurrences[row["destination"]],
         _load(row["flags_json"]), None if row["decision_id"] is None else links[row["decision_id"]])
        for row in db.execute("SELECT event.* FROM generation_event JOIN event "
                              "ON event.id=generation_event.event_id WHERE generation_id=?", (generation,))
    ))
    collect("intervals", (
        (row["path"], row["from_seq"], row["to_seq"], row["status"], row["blob_format"],
         row["blob_digest"], row["object_type"], row["mode"],
         tuple(sorted((definitions[binding["definition_id"]], seeds[binding["eid"]])
                      for binding in db.execute("SELECT * FROM binding WHERE binding_set_id=?",
                                                (row["binding_set_id"],)))))
        for row in db.execute("SELECT * FROM file_state WHERE generation_id=?", (generation,))
    ))
    collect("coverage", (
        (row["sequence"], row["path"], row["category"], row["reason"], row["omitted"], row["count_known"])
        for row in db.execute("SELECT * FROM coverage WHERE generation_id=?", (generation,))
    ))
    collect("departures", (
        (row["sequence"], seeds[row["eid"]], occurrences[row["occurrence_id"]], row["status"], row["reason"])
        for row in db.execute("SELECT * FROM departure WHERE generation_id=?", (generation,))
    ))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with ExitStack() as resources:
        before = facts(resources.enter_context(IndexStore.open(args.baseline)))
        after = facts(resources.enter_context(IndexStore.open(args.candidate)))
    result = {"passed": before == after,
              "interpretation_ids": "excluded deliberately; lineage compared by exact seed occurrence/reason, not similarity",
              "categories": {key: {"equal": before[key] == after[key],
                                   "baseline_records": sum(before[key].values()),
                                   "candidate_records": sum(after[key].values())} for key in before}}
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
