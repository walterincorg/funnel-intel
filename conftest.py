"""Root conftest — stubs out external dependencies for unit tests.

The local supabase/ directory (migrations + config.toml) is a namespace
package that shadows the pip-installed supabase package on sys.path, causing
`from supabase import create_client` to fail in backend/db.py at import time.
We inject a lightweight stub into sys.modules before any test module is
collected so tests that monkeypatch get_db at runtime work without a live DB.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock

if "backend.db" not in sys.modules:
    _db_stub = ModuleType("backend.db")
    _db_stub.get_db = MagicMock()
    sys.modules["backend.db"] = _db_stub
