"""Re-export М11's test fakes (FakeClusterStore, cam) without importing a
second package named `tests`."""
import importlib.util
import os
import sys

_cands = [os.path.join(p, "tests", "conftest.py") for p in sys.path if p.endswith("clustervms")]
for _c in _cands:
    if os.path.exists(_c):
        _spec = importlib.util.spec_from_file_location("clustervms_conftest", _c)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        FakeClusterStore, cam = _mod.FakeClusterStore, _mod.cam
        break
else:
    raise ImportError("clustervms/tests/conftest.py not found on sys.path")
