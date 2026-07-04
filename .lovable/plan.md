## Fix: `from __future__` SyntaxError in `run_app.py`

Python requires `from __future__ import ...` to appear before any other statement (only module docstring and comments may precede it). Currently `APP_VERSION = "4.8"` on line 23 sits before the `from __future__ import annotations` on line 24, which is a hard SyntaxError.

### Change

In `desktop/nse_quant_engine/run_app.py`, move `APP_VERSION = "4.8"` to sit **after** the `from __future__ import annotations` line.

Result (lines 22–26):
```python
"""
from __future__ import annotations

APP_VERSION = "4.8"
import sys
```

No other files or logic change. Header pill / About dialog continue to read `APP_VERSION`, so the displayed version remains v4.8.
