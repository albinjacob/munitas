"""Lets a script at the repository's top, or one that put the repository's root on its path, say `import pgdb`.

The database layer is platform/pgdb.py, which the API image carries as /app/pgdb.py. A script run from the host reaches it through here: this file loads that
one and stands in for it, so there is one implementation and `pgdb` means the same thing wherever it is imported.
"""

import importlib.util
import sys
from pathlib import Path

_real = Path(__file__).resolve().parent / "platform" / "pgdb.py"
_spec = importlib.util.spec_from_file_location("pgdb", _real)
_module = importlib.util.module_from_spec(_spec)
sys.modules["pgdb"] = _module          # the name now means the real module, for this import and every later one
_spec.loader.exec_module(_module)
