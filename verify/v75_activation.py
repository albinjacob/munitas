"""U75: approved is not the same as active, and the platform closes the gap.

An allowed storage grant takes effect only once a print of the permissions
document that contains it succeeds. Every print is recorded in
storage_permission_print, and whether access is in effect, how long printing
has been failing and when to retry are all read from those rows.

Runs inside the munitas-api container. A print is made to fail by replacing
the compile step in this process only, so the failure is recorded in the
shared table exactly as a real one would be, while the running API, whose own
prints still work, plays the activator that has to notice and recover. The
failure a caller actually sees (HTTP 202, a run parking and resuming) needs
storage itself to be down, and is proved by the host check
verify/v76_activation_live.py.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from app import db as app_db  # noqa: E402
from app import grants as app_grants  # noqa: E402
from app import seaweed as app_seaweed  # noqa: E402

from common import (check, db, fixture_tenant, heading, require_api,  # noqa: E402
                    summary)

# Long enough for one retry (10 s after a failure) plus one activator tick
# (every 10 s), with room for a slow tick.
ACTIVATOR_WAIT_SECONDS = 60

# The failures this script makes up, by print id. They are removed when it
# ends, however it ends: the print history is the platform's record of what
# actually happened to storage permissions, and a made-up outage left in it
# would read as a real one. Only these ids are touched; the activator's real
# prints made while recovering from them stay.
SIMULATED: list[int] = []


def newest_print() -> dict | None:
    with db() as conn:
        return conn.execute(
            "select * from storage_permission_print order by id desc limit 1"
        ).fetchone()


def prints_after(print_id: int) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select * from storage_permission_print where id > %s order by id",
            (print_id,),
        ).fetchall()


def failing_print(exc: Exception) -> dict:
    """Make one print fail with `exc`, in this process only, and return its row."""
    real = app_grants._reconcile

    def broken(armed):
        raise exc

    app_grants._reconcile = broken
    try:
        app_grants.reconcile(trigger="manual")
    except Exception:
        pass
    finally:
        app_grants._reconcile = real
    row = newest_print()
    SIMULATED.append(row["id"])
    return row


def wait_for_activator(after_id: int) -> dict | None:
    deadline = time.monotonic() + ACTIVATOR_WAIT_SECONDS
    while time.monotonic() < deadline:
        for row in prints_after(after_id):
            if row["trigger"] == "activator" and row["succeeded"]:
                return row
        time.sleep(1)
    return None


def remove_simulated() -> None:
    """Delete this run's made-up failures, then make sure the newest print is
    a real success, so nothing is left reading as failing."""
    if SIMULATED:
        with db() as conn:
            conn.execute("delete from storage_permission_print where id = any(%s)",
                         (SIMULATED,))
    if app_grants.activation_status()["failing"]:
        app_grants.reconcile(trigger="manual")


def main() -> int:
    require_api()
    app_db.pool.open()
    try:
        checks()
    finally:
        remove_simulated()

    heading("U75i: the made-up failures are removed afterwards")
    with db() as conn:
        left = conn.execute(
            "select count(*) as n from storage_permission_print where id = any(%s)",
            (SIMULATED,)).fetchone()["n"]
    check(f"none of the {len(SIMULATED)} simulated prints remains in the history",
          bool(SIMULATED) and left == 0, f"{left} left")
    check("and activation reports nothing failing",
          app_grants.activation_status()["failing"] is False)
    return summary("U75")


def checks() -> None:

    # --- U75a: every print is recorded ------------------------------------
    heading("U75a: a successful print is recorded with what it wrote")
    before = time.time()
    result = app_grants.reconcile(trigger="manual")
    row = newest_print()
    check("the print is recorded as succeeded, trigger 'manual'",
          bool(row) and row["succeeded"] is True and row["trigger"] == "manual",
          f"{row and {k: row[k] for k in ('id', 'trigger', 'succeeded')}}")
    check("with the number of identities the written document held",
          bool(row) and row["identities"] == result["identities"],
          f"row {row and row['identities']}, result {result['identities']}")
    check("and its finish time, with no failure reason",
          bool(row) and row["finished_at"] is not None and row["reason"] is None)

    # --- U75b: active means a successful print started after --------------
    heading("U75b: a decision is active once a successful print started after it")
    started = app_grants.last_success_started_at()
    check("last_success_started_at is that print's start",
          bool(row) and started == row["started_at"], f"{started}")
    check("something recorded before that print is active",
          app_grants.is_active(datetime.fromtimestamp(before - 5, timezone.utc)))
    check("something recorded now is not active yet (negative)",
          not app_grants.is_active(datetime.now(timezone.utc)))

    # --- U75c: the retry spacing ------------------------------------------
    heading("U75c: retries wait 10 s, then twice as long each time, at most 5 minutes")
    delays = [app_grants.retry_delay_seconds(n) for n in (1, 2, 3, 5, 6, 12)]
    check("1, 2, 3, 5, 6 and 12 failures wait 10, 20, 40, 160, 300 and 300 s",
          delays == [10, 20, 40, 160, 300, 300], f"{delays}")

    # --- U75d: an upload into an organisation whose key is active prints nothing
    heading("U75d: an upload whose key already works does not rebuild anything")
    tenant = fixture_tenant()
    app_seaweed.admin_client(tenant)  # the first may print if the key is new
    check("the organisation's upload key is active",
          app_grants.ingest_is_active(tenant))
    mark = newest_print()["id"]
    app_seaweed.admin_client(tenant)
    app_seaweed.admin_client(tenant)
    check("two more uploads' clients print nothing",
          prints_after(mark) == [], f"{len(prints_after(mark))} new prints")

    # A key that is not active yet, meeting a print that fails, is refused
    # with words saying what is happening. Simulated in this process: making a
    # genuinely new organisation would reserve storage volumes from a pool the
    # suite has already exhausted once.
    real_active, real_compile = app_grants.ingest_is_active, app_grants._reconcile

    def storage_down(armed):
        raise app_seaweed.StorageUnavailable("U75 simulated filer outage")

    app_grants.ingest_is_active = lambda tenant_id: False
    app_grants._reconcile = storage_down
    try:
        app_seaweed.admin_client(tenant)
        refused = None
    except app_seaweed.StorageUnavailable as exc:
        refused = str(exc)
    finally:
        app_grants.ingest_is_active, app_grants._reconcile = real_active, real_compile
    check("a first upload whose print fails is refused honestly",
          refused == "This organisation's storage is being set up. Try again shortly.",
          f"{refused!r}")
    failed = newest_print()
    SIMULATED.append(failed["id"])
    check("and that failed print is recorded as retryable",
          failed["succeeded"] is False and failed["retryable"] is True,
          f"{failed['reason']!r}")

    # --- U75e: a retryable failure is retried by the activator ------------
    heading("U75e: after a failure that retrying can fix, the platform retries by itself")
    failed = failing_print(app_seaweed.StorageUnavailable("U75 simulated filer outage"))
    status = app_grants.activation_status()
    check("the failure is recorded, retryable, with its reason",
          failed["succeeded"] is False and failed["retryable"] is True
          and "U75 simulated" in (failed["reason"] or ""))
    check("activation reports failing, with a retry time",
          status["failing"] and status["retry_due_at"] is not None,
          f"retry_due_at {status['retry_due_at']}")
    check("a short failure raises no alert (negative)", status["alert"] is False)
    recovered = wait_for_activator(failed["id"])
    check(f"the activator printed successfully within {ACTIVATOR_WAIT_SECONDS} s",
          recovered is not None,
          f"print {recovered and recovered['id']} at {recovered and recovered['started_at']}")
    check("and activation no longer reports failing",
          app_grants.activation_status()["failing"] is False)

    # --- U75f: a failure retrying cannot fix is left for a person ---------
    heading("U75f: a failure retrying cannot fix alerts at once and is not retried")
    failed = failing_print(app_grants.UnsafeProjection("U75 simulated guard refusal"))
    status = app_grants.activation_status()
    check("the failure is recorded as not retryable",
          failed["succeeded"] is False and failed["retryable"] is False)
    check("activation raises the alert at once, with no retry time",
          status["alert"] is True and status["retry_due_at"] is None,
          f"alert {status['alert']}, retry_due_at {status['retry_due_at']}")
    time.sleep(25)
    retried = [r for r in prints_after(failed["id"]) if r["trigger"] == "activator"]
    check("the activator made no attempt in 25 s (negative)", retried == [],
          f"{len(retried)} activator prints")
    # A person fixes the cause and prints; here there was nothing to fix.
    app_grants.reconcile(trigger="manual")
    status = app_grants.activation_status()
    check("a successful print clears the alert",
          status["failing"] is False and status["alert"] is False)

    # --- U75g: a failure retrying can fix alerts once it has lasted 5 minutes
    heading("U75g: an outage that retrying can fix is raised after 5 minutes")
    # The rule is checked with the times handed to it. Waiting five real
    # minutes would make every suite run five minutes longer, and moving a
    # made-up failure back in time puts it before the last success, where it
    # rightly stops counting as part of the current outage. Where the outage
    # starts is read from the history by activation_status, checked in U75e.
    from datetime import timedelta
    since = datetime.now(timezone.utc)
    # Stated here rather than read from the platform: a check that takes its
    # expectation from the code it checks passes whatever the code says.
    # Five minutes was decided in review.
    limit = 300
    check(f"a retryable failure {limit - 30} s old raises no alert (negative)",
          app_grants.needs_alert(True, since, since + timedelta(seconds=limit - 30)) is False)
    check(f"at {limit + 30} s it does",
          app_grants.needs_alert(True, since, since + timedelta(seconds=limit + 30)) is True)
    check("a failure retrying cannot fix raises it at once",
          app_grants.needs_alert(False, since, since) is True)

    # --- U75h: the lock timing out counts as retryable --------------------
    heading("U75h: the only failures not retried are the ones only a person can fix")
    lock = app_grants.LockUnavailable("x")
    guard = app_grants.UnsafeProjection("x")
    check("a busy lock is retryable", lock.retryable is True)
    check("a guard refusal is not", guard.retryable is False)


if __name__ == "__main__":
    sys.exit(main())
