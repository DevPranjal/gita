"""Count renumbering false alarms: same-named siblings reported as changed
whose content is present, unchanged, in the parent revision under another slot.

Run against two checkouts to compare. Loose matching (content found anywhere in
the file) overcounts badly -- a new test callback identical to an existing one is
a real addition -- so this restricts to the same name group.
"""

import re
import sys

from gita import diff_revisions
from gita.entities.store import TREES
from gita.revisions import _tree_at
from gita.vcs.git import Repo

ORDINAL = re.compile(r"#\d+$")


def base(entity_id: str) -> str:
    return ORDINAL.sub("", entity_id)


def main(corpus: str) -> None:
    strict = loose = examined = commits = 0
    for name in ("got", "gin", "flask", "ripgrep", "express"):
        repo = Repo(f"{corpus}/{name}")
        try:
            records = repo.walk(limit=25)
        except Exception:
            continue
        for record in records:
            commits += 1
            changeset = diff_revisions(repo, record.parent, record.sha,
                                       changed=record.files)
            material = changeset.material()
            examined += len(material)
            cache: dict[str, dict[str, str]] = {}
            for change in material:
                if not ORDINAL.search(change.entity.id):
                    continue
                path = change.entity.path
                if path not in cache:
                    tree = _tree_at(repo, record.parent, path)
                    cache[path] = ({e.content_hash: e.id for e in tree.entities.values()}
                                   if tree else {})
                twin = cache[path].get(change.entity.content_hash)
                if not twin:
                    continue
                loose += 1
                if base(twin) == base(change.entity.id):
                    strict += 1
            if len(TREES) > 400:
                TREES.clear()

    print(f"commits           {commits}")
    print(f"material changes  {examined}")
    print(f"loose flags       {loose}")
    print(f"renumbering flags {strict}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "spikes/attribution/corpus")
