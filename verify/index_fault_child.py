"""Isolated owned crash fixture: pause with uncommitted evidence before publication."""

import json
from pathlib import Path
import sys
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gita.index.builder import BuildLimits, build
from gita.index.store import IndexStore
from gita.vcs.git import CommittedRepo


def main():
    root, database, tip, marker = sys.argv[1:]
    publish = IndexStore.publish_generation

    def pause(store, generation, **kwargs):
        # This hook runs AFTER parsing/matching/persistence and BEFORE publication,
        # while the outer write transaction owns its uncommitted WAL.
        Path(marker).write_text(json.dumps({"generation": generation, "phase": "before_publish"}))
        sleep(60)
        return publish(store, generation, **kwargs)

    IndexStore.publish_generation = pause
    with CommittedRepo(Path(root)) as repository, IndexStore.open(Path(database), read_only=False) as store:
        build(repository, store, ref=tip, limits=BuildLimits(workers=2))


if __name__ == "__main__":
    main()
