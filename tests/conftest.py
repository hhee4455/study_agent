"""pytest conftest — sys.path setup + dependency stubs for isolated test env."""
from __future__ import annotations

import sys
import types
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PARENT = _THIS_DIR.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))


def _stub_if_missing(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    try:
        import importlib
        importlib.import_module(name)
        return
    except Exception:
        pass
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


_stub_if_missing("lead.dashboard", collect_state=lambda ws_root: {})
