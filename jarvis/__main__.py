"""Allow ``python -m jarvis`` from the ``v2`` directory."""

from __future__ import annotations

import sys
from pathlib import Path

# When executed as ``python -m jarvis`` the parent directory is already on the
# path, but this keeps ``python jarvis/__main__.py`` working too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run import run  # noqa: E402

if __name__ == "__main__":
    run()
