"""Pytest root conftest — puts the repo root on sys.path so tests can
import `backend.*` without needing PYTHONPATH set externally.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
