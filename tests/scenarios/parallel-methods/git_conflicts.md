# What traditional git does

Both sides appended a new method to the end of the class body. Git sees
edits on the same trailing lines (or, worse, on the same blank line and
closing-of-file region) and reports a conflict:

```
class Inventory:
    def __init__(self) -> None: ...
    def add(self, sku, qty): ...
<<<<<<< ours
    def remove(self, sku, qty): ...
=======
    def count(self, sku): ...
>>>>>>> theirs
```

# What gitpp must do

Treat `ClassDef.body` as an **unordered set of members keyed by stable_id**.

- `base` set = { `__init__`, `add` }
- `ours` set = { `__init__`, `add`, `remove` }
- `theirs` set = { `__init__`, `add`, `count` }
- Merged set = base ∪ (ours − base) ∪ (theirs − base) =
  { `__init__`, `add`, `remove`, `count` }

Render in a stable order: members from base first (in their original order),
then ours-additions in their original order, then theirs-additions.

This is the **simplest** of the three tests and should be the first to pass.
