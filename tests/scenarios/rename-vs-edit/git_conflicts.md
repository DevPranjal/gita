# What traditional git does

`ours` renamed `get_user` → `fetch_user` at 4 sites (definition + 3 calls).
`theirs` added validation and an `"active"` field inside the body of
`get_user`. Git sees overlapping line edits at every call site that was
renamed AND at the function definition. Conflicts everywhere:

```
<<<<<<< ours
def fetch_user(user_id: int) -> Optional[dict]:
    record = _db_lookup(user_id)
=======
def get_user(user_id: int) -> Optional[dict]:
    if user_id < 0:
        raise ValueError("user_id must be non-negative")
    record = _db_lookup(user_id)
>>>>>>> theirs
```

…and similar markers at each call site.

# What gitpp must do

1. Identify `get_user` and `fetch_user` as the **same symbol** via stable
   ID (assigned at first sighting; preserved through the rename).
2. Express `ours` as a single semantic op: `rename(symbol=S, "get_user" →
   "fetch_user")`.
3. Express `theirs` as edits on the **body subtree** of the function whose
   stable_id is S. These touch different fields/children than the `name`
   field, so they do not conflict with the rename.
4. Apply both. Result: function named `fetch_user`, with validation +
   `"active"` field, and all call sites updated to `fetch_user(...)`.

This is the **load-bearing** test. If this passes, the project is real.
