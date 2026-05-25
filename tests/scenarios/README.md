# Acceptance scenarios

Each subdirectory is a 3-way merge test case. The contract:

```
gitpp merge --base base.py --ours ours.py --theirs theirs.py   →   expected.py
```

with **zero conflicts**.

For comparison, `git_conflicts.md` shows what `git merge-file` produces for
the same inputs — typically a conflict-marked file. The whole point of `gitpp`
is to turn those into clean merges.

## Scenarios

| Directory                                                    | Tests                                                  |
|--------------------------------------------------------------|--------------------------------------------------------|
| [`import-reorder-add/`](./import-reorder-add/)               | Unordered-set merge of import group                    |
| [`rename-vs-edit/`](./rename-vs-edit/)                       | Symbol rename + independent body edit                  |
| [`parallel-methods/`](./parallel-methods/)                   | Unordered-set merge of class members                   |
