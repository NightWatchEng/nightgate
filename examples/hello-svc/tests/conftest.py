"""Make hello_svc importable regardless of pytest's rootdir/cwd."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
