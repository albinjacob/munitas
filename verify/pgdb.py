"""Lets every check say `import pgdb` wherever it runs, in whatever order it imports things.

The database layer is platform/pgdb.py on the host and /app/pgdb.py in the API container, where the checks run from a read-only mount of this folder and
nothing else is on their path. A script's own folder is first on its path, so this file is found first, loads the real module and stands in for it: there is
one implementation, and `pgdb` means the same thing in every check.
"""

import importlib.util
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_real = next((p for p in (_here.parent / "platform" / "pgdb.py", Path("/app/pgdb.py")) if p.exists()), None)
if _real is None:
    raise ImportError("the database layer was found neither in platform/ nor in /app")
_spec = importlib.util.spec_from_file_location("pgdb", _real)
_module = importlib.util.module_from_spec(_spec)
sys.modules["pgdb"] = _module          # the name now means the real module, for this import and every later one
_spec.loader.exec_module(_module)
