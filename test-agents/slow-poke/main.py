"""slow-poke: demonstrates the sandboxed run's wall-clock ceiling.

Every sandboxed run gets a caller-set (or default, 300s) timeout. This
agent just sleeps well past whatever ceiling the run was started with.
worker/sandbox_run.py's _run_and_wait kills the container from outside it
when that happens, and the run is recorded 'failed' with a reason naming
the ceiling -- never left 'running' forever, and never silently reported
as having succeeded. Mirrors verify/v55_sandboxed_agent_run.py's own hang
fixture.

If you ever see this JSON in a run's findings, the ceiling did not fire --
something is wrong, because this process should have been killed first.
"""

import json
import time

time.sleep(600)
print(json.dumps({"marker": "slow-poke", "note": "should never print this"}))
