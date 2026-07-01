# Task 02: Bug fix from stack trace

- **Cluster:** code & cluster
- **Fixtures:** fixtures/stack-trace.txt
- **Dimensions:** correctness, faithfulness, format

## Prompt

Below is a Python traceback from a service in this homelab. Read it, identify the
exact line and root cause, and propose the minimal fix. Do not rewrite the
function — give the smallest change that resolves the error and say why.

```
<contents of fixtures/stack-trace.txt>
```

## Ground truth / rubric

The trace is a `KeyError: 'PG_PASSWORD'` raised from `os.environ['PG_PASSWORD']`
inside `connect_db()` at `memory_store.py:42`. Root cause: the variable is read
from `os.environ` but the caller passed it via `subprocess.run(env=...)` without
`export`, OR the key simply isn't set — the minimal fix is `os.environ.get(...)`
with an explicit error, or reading from the injected config rather than raising
a bare KeyError.

Correct answer:
- Names `memory_store.py:42` and the `KeyError` on `PG_PASSWORD`.
- Proposes `os.environ.get('PG_PASSWORD')` with a clear failure, or sourcing from
  the config object already in scope — NOT a broad refactor.
- Explains the *why* (bare `[...]` raises; `.get()` + explicit check is the minimal safe change).

Trap: recommending a large rewrite, or "add a try/except" that swallows the error
silently, both score low on correctness.

## Scoring

- **correctness (0–3):** 3 = right line + right root cause + minimal fix; 2 = right line, heavier-than-needed fix; 1 = wrong line but plausible fix; 0 = misidentifies.
- **faithfulness (0–3):** 3 = fix fits the shown code; 0 = invents functions/fields not in the trace.
- **format (auto):** mentions `memory_store.py` and `PG_PASSWORD`; output is a targeted diff or ≤5-line snippet, not a full-file rewrite.
