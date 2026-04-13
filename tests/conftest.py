"""
Stub out composio SDK before any backend module is imported.
This allows tests to run without composio-core installed.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_composio_stub():
    if "composio" not in sys.modules:
        stub = ModuleType("composio")
        stub.ComposioToolSet = MagicMock
        stub.Action = MagicMock()
        sys.modules["composio"] = stub


_install_composio_stub()
