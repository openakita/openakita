# `_audit_lib/` — shared exploratory-audit helpers

Sprint-8 P0-C (v19 audit `_orgs_business_capability_audit_v8.md`
§5.3 + §8.3) extracts two things that every `_v*_biz/_lib.py` copy
shipped with the same bug or hard-coded magic value:

1. **`log_grep.py`** — line-timestamp-aware log grep that fixes the
   v18/v19 `No handler mapped` false-positive class.
2. **`timeouts.py`** — recommended httpx timeouts for v20+ test
   scripts so the test client stops short-cutting legitimate slow
   LLM responses.

## Usage from a v20+ exploratory script

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _audit_lib import RECOMMENDED, grep_logs_since

cutoff_ts = 1779779270.256154
hits = grep_logs_since(cutoff_ts, "No handler mapped for tool")
client = httpx.Client(timeout=RECOMMENDED.client_default_s)
```

## Why a folder at repo root, not under `tests/` or `src/`

`_v*_biz/` exploratory scripts already live at repo root and import
each other via `sys.path` insertion. Putting the shared helper in
`src/openakita/` would couple per-sprint test artefacts to the
production import graph; putting it under `tests/` would either
require pytest collection or a sibling `conftest.py`. Repo-root
keeps it next to its callers.

## Coverage

Unit tests live in `tests/audit/test_log_grep.py`. They pin the
line-timestamp filter, the file-mtime fast-path, and the continuation-
line inheritance so a future refactor cannot regress to the v17-v19
file-mtime-only behaviour without flipping a red.
