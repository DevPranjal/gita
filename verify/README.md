# Verifying gita yourself

You are relying on me for the implementation. This directory exists so you do
not have to rely on me for the *evidence*.

Two commands. The first checks the claims; the second lets you handle the thing
directly.

```powershell
.venv\Scripts\python.exe verify\check.py          # is what I said true?
.venv\Scripts\python.exe verify\check.py --demo   # let me see for myself
```

---

## 1. The harness

```powershell
.venv\Scripts\python.exe verify\check.py
```

Prints one line per claim, with the measured value and a verdict. Exit code 0
only if every check on a shipped stage passed.

```
WS1   Enumeration                                                SHIPPED
  merge fallback removed                  1 git process (old path: 6) PASS
  file lists identical to git             11 commits, 0 mismatched    PASS
  answers unchanged by the speedup        11 commits compared         PASS
  real history: flask                     40 commits, 0 mismatched    PASS

WS2   Store                                                NOT BUILT YET
...

Year 1 acceptance
  history of a renamed and moved function gita 2 of 5  (git: 5 of 5)  EXPECTED FAIL
```

### Why this is worth more than my test suite

**It does not run my tests.** If I write a bad test, running it again proves
nothing. Every check here is re-derived from scratch, and wherever gita and git
can answer the same question, **git is the oracle** — gita's file lists are
compared against `git diff --name-status`, commit by commit, and any
disagreement is a failure.

**It includes a negative control.** The "merge fallback removed" line forces the
*old* code path back on and measures both, so you see 6 processes become 1
rather than taking a number on faith.

**It can fail.** See §4 — you can prove that yourself in about thirty seconds.

**Unbuilt stages say so.** A stage is detected by whether its module exists, so
nothing can pass vacuously by not being written yet.

---

## 2. Hands-on

```powershell
.venv\Scripts\python.exe verify\check.py --demo
```

Builds a five-commit repository where one function is born, edited, **renamed**,
**moved to another file**, and edited again. The ground truth is exact by
construction — every one of those five commits touches the same function.

It prints the path and the commands to try. The short version:

```powershell
cd $env:TEMP\gita-verify-refactor

git log --follow --oneline -- security.py
# five commits. git finds them all, by guessing from file similarity.

Q:\projects\gita\.venv\Scripts\python.exe -m gita history check_token
# two. gita loses the thread at the rename.

Q:\projects\gita\.venv\Scripts\python.exe -m gita diff HEAD~3 HEAD~2 --brief
# check_token  [renamed]     <- it saw the rename

Q:\projects\gita\.venv\Scripts\python.exe -m gita diff HEAD~2 HEAD~1 --brief
# check_token  [moved]       <- and the move
```

**That contrast is the entire argument for Year 1.** The engine detects the
rename and the move correctly, one commit at a time — and then throws the link
away, because today an entity's identity *is* its location
(`auth.py::validate_token`). Rename the function and the identity changes.

Right now gita is worse than `git log --follow` at this. When WS-3 and WS-4
land, `gita history check_token` returns five, and the harness line above flips
from `EXPECTED FAIL` to `PASS`.

---

## 3. What each stage adds

Re-run the harness after each one. New lines appear; nothing already passing
should stop passing.

| stage | what you will be able to check |
| --- | --- |
| **WS-1** *(done)* | merge walks cost one git process; file lists match git exactly |
| **WS-2** | `.git/gita/index.sqlite` exists; a corrupt or stale index is detected and discarded rather than trusted |
| **WS-3** | `gita index` builds; **building twice produces byte-identical identifiers**; incremental update equals a full rebuild |
| **WS-4** | `gita id check_token` returns a stable id; `gita id validate_token` returns **the same id**; history returns 5 of 5 |
| **WS-5** | identity precision and recall reported against two independent answer keys |

The WS-3 line is the one to watch. If building the same history twice produces
different identifiers, everything above it is unreliable, and I would rather you
saw that directly than took my word that it holds.

---

## 4. Catching me out

A verification harness you cannot break is decoration. Prove this one bites:

```powershell
# revert the WS-1 fix without touching anything else
(Get-Content src\gita\vcs\git.py -Raw) `
  -replace 'if self\.supports_diff_merges else \[\]', 'if False else []' `
  | Set-Content src\gita\vcs\git.py -NoNewline

.venv\Scripts\python.exe verify\check.py
echo $LASTEXITCODE          # 1

git checkout -- src/gita/vcs/git.py
.venv\Scripts\python.exe verify\check.py
echo $LASTEXITCODE          # 0
```

With the fix reverted you should see three lines flip to `FAIL`, including
`real history: flask   40 commits, 15 mismatched`.

Worth noting what happens to the *fourth* line: "merge fallback removed" still
reports `PASS`, because that break makes gita ask for fewer processes **and get
the wrong answer**. A process count alone would have been fooled. The
correctness checks are what caught it — which is the reason they compare against
git rather than against my expectations.

---

## 5. Independent checks I have not automated

Worth doing occasionally, because they are the ones most likely to embarrass me:

```powershell
.venv\Scripts\python.exe -m pytest -q          # ~6 min, expect 574 passed
.venv\Scripts\python.exe -m gita diff --brief  # gita reviewing its own changes
git log --oneline first-principles ^main       # what I have actually committed
```

And the blunt one — pick any number in `specs/entity-index/` or
`scratch/SPEC-ENTITY-INDEX.md` and ask me where it came from. Every measured
figure in those documents traces to a script in `scratch/probe_*.py` that you
can run. If one does not, that is a finding.
