# What traditional git does

`git merge-file ours.py base.py theirs.py` reports a conflict on the import
block, because both sides touched overlapping lines:

```
<<<<<<< ours.py
import json
import os
import sys
=======
import os
import json
import sys
import logging
>>>>>>> theirs.py
```

Resolution requires a human to manually reconcile.

# What gitpp must do

Treat the import block as an **unordered set of import-statement nodes**,
keyed by `(module, imported_names, alias)`. Set union of {os, json, sys}
∪ {os, json, sys, logging} = {os, json, sys, logging}. Render sorted.

The body-level addition of `logging.basicConfig(...)` is a separate
non-conflicting insertion into `main`'s body and merges trivially.
