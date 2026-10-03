"""What the storage permissions document should contain, computed from the register.

The document in SeaweedFS's filer used to be edited: read it, add a line, write
it back. Three faults came from that one habit. A lease's expiry withdrew
nothing, because withdrawing is a separate act and `revoke_prefix` was never
called by anything. Two grants issued in the same moment lost one of each
other, because the write is a whole-document post with no version check. And a
grant named a role rather than a person, so one holder could not be cut without
cutting another.

This module answers a different question: not "what should change" but "what
should the whole document say". The answer is compiled from the policy (which
roles hold a key and their standing access), configuration (the keys) and the
register (buckets, justified grants, minted identities), never from the
document's current contents. reconcile() is the only writer, and it holds a
lock across compiling and writing, so two callers cannot interleave.

Deliberately not in seaweed.py. That module is about talking to SeaweedFS.
Deciding who may read what is a question about leases and policy that merely
ends up written there.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from . import config, db, opa, seaweed


class UnsafeProjection(Exception):
    """The computed document was refused before it could be written.

    A printer that writes the whole document can, if it is wrong, remove
    everyone's access at once, where editing one line could only ever break one
    line. That risk is created by this design rather than inherited from the
    old one, so it gets a guard rather than care.

    `retryable` says whether trying again could succeed without a person doing
    anything: true for a service that was unreachable or a lock that was busy,
    false for the guard refusing a print or a role with no configured key,
    which the activator therefore leaves for an administrator.
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LockUnavailable(UnsafeProjection):
    """Another reconcile held the document for longer than the bounded wait."""

    def __init__(self, message: str):
        super().__init__(message, retryable=True)


# How much of the live lease-derived access one print may remove before it is
# treated as a fault rather than as housekeeping. The first print is expected to
# remove a great deal, because 224 expired leases have never been withdrawn, so
# the guard is armed by the caller rather than always on.
#
# Set below the measured first-run shrink rather than above it. Re-measured on
# this stack the first honest print removes 47.2% of prefix grants (2356 down to
# 1244); the net is under half because floor-justified prefixes the projection
# re-adds offset some of the expired-lease removals. A ceiling of 0.4 keeps the
# first cleanup a deliberate, armed=False act, and lets routine reconciles, which
# remove close to nothing, pass on their own.
#
# Lease expiry is exactly the shape this exists to tolerate as "routine" --
# and it happens continuously by the clock, on every real deployment, not
# only during a first cleanup or on a dev machine. `_reconcile()` therefore
# checks a second time, only when this ceiling is first exceeded, whether
# the excess traces entirely to leases that have since expired or been
# revoked: if recomputing as though they had not brings the shrink back
# under this same ceiling, the print proceeds without a person, recorded as
# `auto_explained` rather than `reason`. Only a shrink this cannot account
# for still stops here and waits for one.
MAX_SHRINK = 0.4


def _buckets() -> list[str]:
    """Every bucket the platform writes to, which is every recorded one.

    The shared bucket used to be added unconditionally, because a tenant
    on it had no row to be found by. Every tenant now has a row, so it
    appears here exactly when some tenant is genuinely still on it and
    stops appearing once none is, rather than being asserted forever.
    """
    rows = db.all_rows(
        "select bucket from tenant_storage_provision where backend = 'seaweedfs'"
    )
    return sorted({r["bucket"] for r in rows})


def justified_pairs(*, as_of=None) -> set[tuple[str, str, str]]:
    """Every (role, bucket, storage_prefix) that still deserves a grant.

    Bounded by what was actually asked for. `access_decision` records each
    credential the platform granted, so the candidate set is those pairs and
    never every version a role's floor would permit, which for one tenant here
    would be over a thousand prefixes nobody requested.

    A pair survives on either of the two grounds the policy engine itself
    allows on. The floor is read from the policy bundle rather than restated,
    because anything that copies `role_floor` acquires the ability to disagree
    with it.

    The bucket is carried from the query, read from the same row
    `seaweed._resolve_bucket` reads. The join is inner rather than outer on
    purpose: every tenant reaching this point holds data, and a tenant
    holding data has a row, backfilled by schema.sql or written when it
    first wrote. A missing row would drop that tenant's grants from the
    document, which fails loudly on the next read, and that is the right
    failure. Filling one in from the shared bucket, as this used to, hands
    out a grant against a bucket the objects are not in. Resolving it from
    the prefix's first segment would be a guess too, because that segment
    is a tenant id, not a bucket.

    `as_of` is never used to decide what to write, only to measure:
    `_reconcile()`'s shrink guard calls it a second time with the moment of
    the last successful print, so a lease counts as valid if it had not yet
    expired or been revoked *then*, even if it has since. `live` already
    reflects every lease that had already lapsed before that print, so
    without a bound here this would resurrect the platform's entire lease
    history rather than only what changed since -- ignoring status
    outright was tried first and produced a nonsensical negative count,
    caught only by actually running it, not by reasoning about it.
    """
    floors = opa.read_document("munitas/access/role_floor") or {}
    order = opa.read_document("munitas/access/class_order") or {}

    asked = db.all_rows(
        """select distinct unnest(d.principal_roles) as role,
                  v.storage_prefix, v.tenant_id, vc.current_class,
                  d.dataset_version_id,
                  p.bucket
             from access_decision d
             join dataset_version v on v.id = d.dataset_version_id
             join version_class vc on vc.dataset_version_id = v.id
             join tenant_storage_provision p
                    on p.tenant_id = v.tenant_id and p.backend = 'seaweedfs'
            where d.phase = 'grant' and d.allowed
              and v.storage_backend = 'seaweedfs'""",
    )

    if as_of is None:
        lease_status_filter = "where l.revoked = false and (l.expires_at is null or l.expires_at > now())"
        lease_status_params: tuple = ()
    else:
        # Valid at `as_of` and unrevoked then, whatever its status is by
        # now: a lease that had already lapsed before `as_of` was excluded
        # from `live` too, by the print that ran at that moment, so it must
        # stay excluded here or the comparison inflates past what `live`
        # could ever have contained.
        lease_status_filter = (
            "where (l.revoked = false or l.revoked_at > %s) "
            "and (l.expires_at is null or l.expires_at > %s)"
        )
        lease_status_params = (as_of, as_of)
    leased = {
        (row["role"], str(row["dataset_version_id"]))
        for row in db.all_rows(
            f"""select distinct unnest(dir.roles) as role, l.dataset_version_id
                 from access_lease l
                 join directory dir on dir.id = l.principal
                 {lease_status_filter}""",
            lease_status_params,
        )
    }

    keep: set[tuple[str, str, str]] = set()
    for row in asked:
        role, bucket, prefix = row["role"], row["bucket"], row["storage_prefix"]
        by_floor = (order.get(row["current_class"]) is not None
                    and floors.get(role) is not None
                    and order[row["current_class"]] >= floors[role])
        by_lease = (role, str(row["dataset_version_id"])) in leased
        if by_floor or by_lease:
            keep.add((role, bucket, prefix))
    return keep


def justified_write_pairs() -> set[tuple[str, str, str]]:
    """Every (role, bucket, storage_prefix) a real task has legitimately
    reserved to write into.

    The write-side counterpart to justified_pairs() above, and simpler: a
    write_grant row is monotonic (see its own comment in schema.sql), so
    unlike a lease there is no expiry or revocation to filter on here --
    once a real task proved itself and was granted a prefix, that grant
    never needs to be measured against `as_of` either, because nothing ever
    withdraws it. Every row still deserves a grant, always, for as long as
    it exists.
    """
    rows = db.all_rows("select distinct role, bucket, storage_prefix from write_grant")
    return {(r["role"], r["bucket"], r["storage_prefix"]) for r in rows}


def _write_prefix_actions(bucket: str, prefix: str) -> list[str]:
    """The two strings one write grant needs: Write on the objects
    themselves, and on the bare prefix.

    The `Write:` counterpart to _prefix_actions above, and for the same
    reason: SeaweedFS reads `Write:bucket/prefix` as that one path, not the
    objects beneath it, so the wildcard form is what a real PutObject under
    the prefix actually needs and the bare form is what completes it.
    """
    base = prefix.rstrip("/")
    return [f"Write:{bucket}/{base}/*", f"Write:{bucket}/{base}"]


def _prefix_actions(bucket: str, prefix: str) -> list[str]:
    """The four strings one prefix grant needs.

    The trailing `/*` is load bearing and the bare form is too: SeaweedFS reads
    `Read:bucket/prefix` as that one path and not the objects beneath it, so a
    grant without the wildcard appears complete and refuses every GetObject
    under it. Both forms, for read and for list, exactly as `grant_prefix`
    already emitted them.
    """
    base = prefix.rstrip("/")
    return [f"Read:{bucket}/{base}/*", f"Read:{bucket}/{base}",
            f"List:{bucket}/{base}/*", f"List:{bucket}/{base}"]


def _minted_identities(*, as_of=None) -> list[dict]:
    """Every identity minted from a storage_identity row: one per live lease
    with a key of its own, and each tenant's standing ingest identity. Each
    exists exactly while its row does.

    `as_of`: see `justified_pairs()`'s own docstring. Measurement only,
    never used for a document actually written.
    """
    identities: list[dict] = []
    # One identity per active lease that has a key of its own. The where clause
    # is the whole feature: a lease that has expired or been revoked contributes
    # no identity, so the next print does not contain that key and it stops
    # working, while another holder's row is a different row and is untouched.
    from crypto import EnvelopeCrypto
    crypto = EnvelopeCrypto()
    if as_of is None:
        lease_status_filter = "and l.revoked = false and (l.expires_at is null or l.expires_at > now())"
        lease_status_params: tuple = ()
    else:
        lease_status_filter = (
            "and (l.revoked = false or l.revoked_at > %s) "
            "and (l.expires_at is null or l.expires_at > %s)"
        )
        lease_status_params = (as_of, as_of)
    for row in db.all_rows(
        f"""select si.identity_name, si.access_key_id, si.tenant_id,
                  si.secret_ciphertext, si.secret_wrapped_key,
                  v.storage_prefix,
                  p.bucket
             from storage_identity si
             join access_lease l on l.id = si.lease_id
             join dataset_version v on v.id = l.dataset_version_id
             join tenant_storage_provision p
                    on p.tenant_id = v.tenant_id and p.backend = 'seaweedfs'
            where si.ended_at is null
              {lease_status_filter}""",
        lease_status_params,
    ):
        secret = crypto.open(
            row["tenant_id"], row["identity_name"],
            bytes(row["secret_ciphertext"]), bytes(row["secret_wrapped_key"]),
        )
        identities.append({
            "name": row["identity_name"],
            "credentials": [{"accessKey": row["access_key_id"],
                             "secretKey": secret.decode("utf-8")}],
            "actions": _prefix_actions(row["bucket"], row["storage_prefix"]),
        })

    # A tenant's own standing ingest identity. No expiry filter: this exists
    # for as long as the tenant does, not for as long as a lease is live, so
    # unlike the block above it is not conditioned on time. Bucket-wide within
    # the tenant's own bucket, matching r2.admin_client's own scope.
    for row in db.all_rows(
        """select si.identity_name, si.access_key_id, si.tenant_id,
                  si.secret_ciphertext, si.secret_wrapped_key,
                  p.bucket
             from storage_identity si
             join tenant_storage_provision p
                    on p.tenant_id = si.tenant_id and p.backend = 'seaweedfs'
            where si.lease_id is null and si.agent_run_id is null
              and si.backend = 'seaweedfs'
              and si.ended_at is null""",
    ):
        secret = crypto.open(
            row["tenant_id"], row["identity_name"],
            bytes(row["secret_ciphertext"]), bytes(row["secret_wrapped_key"]),
        )
        identities.append({
            "name": row["identity_name"],
            "credentials": [{"accessKey": row["access_key_id"],
                             "secretKey": secret.decode("utf-8")}],
            "actions": [f"{verb}:{row['bucket']}"
                        for verb in ("Read", "Write", "List", "Tagging")],
        })

    # A table job's own key: read and write on the one folder the job reserved, in the one bucket of its organisation, for
    # as long as the job is pending or running and has not expired. It is the only storage key a table worker is ever given.
    # The secret is derived from the job's id (see table_job_secret), so nothing is stored, and the key stops working at
    # the next print after the job ends, which the platform makes happen when it finishes the job.
    #
    # It can also LIST the names of objects in that one bucket, and this is the one place it is wider than its folder. Storage
    # authorises a listing on the bucket, never on a folder inside it (a list on a prefix-limited key is refused, however the
    # prefix is written), and PyIceberg lists before it creates a metadata file, to be sure the file is new. Without it the
    # library cannot make a table at all. A list shows names, not contents: reading and writing stay on the one folder, and
    # another organisation's bucket is not reachable by this key at all. verify/v109_table_jobs.py states both.
    for row in db.all_rows(
        """select j.id::text as id, j.storage_prefix, p.bucket
             from table_job j
             join tenant_storage_provision p on p.tenant_id = j.tenant_id and p.backend = 'seaweedfs'
            where j.status in ('pending', 'running') and j.expires_at > now()"""):
        identities.append({
            "name": table_job_identity(row["id"]),
            "credentials": [{"accessKey": table_job_access_key(row["id"]), "secretKey": table_job_secret(row["id"])}],
            "actions": sorted(set(_prefix_actions(row["bucket"], row["storage_prefix"])
                                  + _write_prefix_actions(row["bucket"], row["storage_prefix"])
                                  + [f"List:{row['bucket']}"])),
        })

    # The catalog's rolling keys. Each identity holds the keys for the previous,
    # the current and the next epoch, so a key a person was handed a moment ago,
    # or one that is about to be asked for, is always already in the document
    # (see catalog_epoch). It is in the document while it was asked for in this
    # epoch or the one before, and, if it rests on a lease, while that lease is
    # live. When neither holds it is simply not compiled, and its keys stop
    # working at the next print.
    now_epoch = catalog_epoch()
    for row in db.all_rows(
        """select ck.identity_name, v.storage_prefix, p.bucket
             from catalog_key ck
             join dataset_version v on v.id = ck.dataset_version_id
             join tenant_storage_provision p
                    on p.tenant_id = v.tenant_id and p.backend = 'seaweedfs'
             left join access_lease l on l.id = ck.lease_id
            where ck.issued_epoch + 1 >= %s
              and (ck.lease_id is null
                   or (l.revoked = false and (l.expires_at is null or l.expires_at > now())))""",
        (now_epoch,),
    ):
        identities.append({
            "name": row["identity_name"],
            "credentials": [{"accessKey": catalog_access_key(row["identity_name"], e),
                             "secretKey": catalog_secret(row["identity_name"], e)}
                            for e in (now_epoch - 1, now_epoch, now_epoch + 1)],
            "actions": _prefix_actions(row["bucket"], row["storage_prefix"]),
        })
    return identities


# ------------------------------------------------- the catalog's rolling keys --
#
# A storage key that works until a lease ends is a bearer credential with a long
# life: anyone holding it can read for as long as the lease lasts. SeaweedFS has
# no short-lived credentials of its own (seaweed.credentials_for says so), so
# this makes them from what it does have, a document the platform rewrites.
#
# Time is cut into epochs, half of CATALOG_KEY_SECONDS long. Each identity's
# secret for an epoch is derived, not stored: an HMAC of the identity and the
# epoch under a key only the platform holds. The document carries three epochs
# at a time (the one before, this one, the next) and is reprinted whenever the
# epoch changes. A key handed out in epoch e is therefore good from then until
# the document for epoch e+2 is written, which is between one and two epochs, so
# between half the setting and all of it. A key can be asked for again whenever
# a client wants a fresh one, and the same decision is made again each time.

def catalog_epoch_seconds() -> int:
    return config.CATALOG_KEY_SECONDS // 2


def catalog_epoch(at: float | None = None) -> int:
    import time
    return int((time.time() if at is None else at) // catalog_epoch_seconds())


def catalog_access_key(identity_name: str, epoch: int) -> str:
    return f"{identity_name}-{epoch}"


def catalog_secret(identity_name: str, epoch: int) -> str:
    import hashlib
    import hmac
    # Derived from the storage administrator's own secret, which already stands
    # for the platform's whole authority over storage, and separated by a label
    # so this use of it can never collide with another.
    root = hmac.new(config.STORAGE_ADMIN[1].encode("utf-8"),
                    b"munitas catalog keys v1", hashlib.sha256).digest()
    return hmac.new(root, f"{identity_name}|{epoch}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def table_job_identity(job_id: str) -> str:
    return f"tj-{job_id[:12]}"


def table_job_access_key(job_id: str) -> str:
    return f"tj-{job_id}"


def table_job_secret(job_id: str) -> str:
    import hashlib
    import hmac
    # Derived the way the catalog's secrets are, from the storage administrator's own secret under a label of its own, so
    # no table job's key can be mistaken for, or computed from, any other kind of key.
    root = hmac.new(config.STORAGE_ADMIN[1].encode("utf-8"), b"munitas table job keys v1", hashlib.sha256).digest()
    return hmac.new(root, job_id.encode("utf-8"), hashlib.sha256).hexdigest()


def catalog_key_for(principal: str, tenant_id: str, version_id: str,
                    lease_id: str | None) -> dict:
    """The key this person gets for this version right now, and whether the
    document must be printed before it works.

    Called only after the access decision has been made and recorded. A key is
    asked for again for as long as the reader needs one, and every ask is
    decided afresh, so access that has ended is not renewed.
    """
    import uuid

    epoch = catalog_epoch()
    before = db.one(
        "select id, identity_name, issued_epoch, lease_id::text as lease_id "
        "from catalog_key where principal = %s and dataset_version_id = %s",
        (principal, version_id))
    if before:
        db.execute(
            """update catalog_key
                  set issued_epoch = greatest(issued_epoch, %s), lease_id = %s
                where id = %s""",
            (epoch, lease_id, before["id"]))
        name = before["identity_name"]
        needs_print = (before["issued_epoch"] < epoch - 1
                       or before["lease_id"] != lease_id)
    else:
        key_id = str(uuid.uuid4())
        name = f"cat-{key_id[:12]}"
        db.execute(
            """insert into catalog_key
                 (id, tenant_id, principal, dataset_version_id, lease_id,
                  identity_name, issued_epoch)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (principal, dataset_version_id) do nothing""",
            (key_id, tenant_id, principal, version_id, lease_id, name, epoch))
        # Another request for the same person and version may have won the race;
        # use whichever row stands.
        row = db.one("select identity_name from catalog_key where principal = %s "
                     "and dataset_version_id = %s", (principal, version_id))
        name = row["identity_name"]
        needs_print = True
    return {
        "identity_name": name,
        "access_key": catalog_access_key(name, epoch),
        "secret_key": catalog_secret(name, epoch),
        # When the document for epoch + 2 is written, at the latest a tick late.
        "expires_at": (epoch + 2) * catalog_epoch_seconds(),
        "needs_print": needs_print,
    }


# The platform's own key. Not a policy role: it provisions buckets and nothing
# reads data with it, so its actions are fixed here, unscoped, as they have
# always been.
ADMIN_IDENTITY = "munitas-admin"
ADMIN_ACTIONS = ("Admin", "List", "Read", "Tagging", "Write")


def storage_roles() -> dict:
    """The roles that hold a storage key, and each one's standing access, as
    the policy defines them (storage_roles in platform/policy/access.rego)."""
    roles = opa.read_document("munitas/access/storage_roles")
    if not roles:
        raise UnsafeProjection(
            "the policy's storage_roles could not be read, so the roles cannot "
            "be compiled. Refusing to write rather than drop every role",
            retryable=True,
        )
    return roles


def identity_names() -> set[str]:
    """The identities that exist without a storage_identity row."""
    return {ADMIN_IDENTITY} | set(storage_roles())


def desired_document(*, as_of=None) -> dict:
    """The whole permissions document, compiled from declared inputs only.

    The policy says which roles hold a key and what each may do across every
    bucket; configuration holds the keys; the register holds the buckets, the
    per-version grants it justifies, and the minted identities. The document
    already in SeaweedFS is never an input, so a fresh install and a running
    one compile the same way, and nothing here can inherit a mistake from a
    previous print.

    `as_of`: see `justified_pairs()`'s own docstring. A document built with
    this set is never written; `_reconcile()` builds one only to measure it
    against `live`, then discards it.
    """
    roles = storage_roles()
    unkeyed = sorted(set(roles) - set(config.ROLE_STORAGE_KEYS))
    if unkeyed:
        raise UnsafeProjection(
            f"the policy names storage roles with no configured key: {unkeyed}. "
            "Refusing to write a document they could not use"
        )
    buckets = _buckets()
    keep = justified_pairs(as_of=as_of)
    # Not measured against `as_of`: see justified_write_pairs()'s own
    # docstring for why a write grant needs no time-bounded replay the way a
    # lease-derived read does.
    write_keep = justified_write_pairs()

    admin_key, admin_secret = config.STORAGE_ADMIN
    identities = [{
        "name": ADMIN_IDENTITY,
        "credentials": [{"accessKey": admin_key, "secretKey": admin_secret}],
        "actions": list(ADMIN_ACTIONS),
    }]
    for role in sorted(roles):
        verbs = roles[role].get("every_bucket") or []
        actions = [f"{verb}:{bucket}" for verb in verbs for bucket in buckets]
        for held_by, bucket, prefix in keep:
            if held_by == role:
                actions.extend(_prefix_actions(bucket, prefix))
        for held_by, bucket, prefix in write_keep:
            if held_by == role:
                actions.extend(_write_prefix_actions(bucket, prefix))
        key, secret = config.ROLE_STORAGE_KEYS[role]
        identities.append({
            "name": role,
            "credentials": [{"accessKey": key, "secretKey": secret}],
            "actions": sorted(set(actions)),
        })
    identities.extend(_minted_identities(as_of=as_of))
    return {"identities": identities}


def identity_for_tenant_ingest(tenant_id: str) -> dict:
    """This tenant's own standing SeaweedFS identity, created once and reused.

    Unlike identity_for_lease, there is no expiry to check on read: this
    identity exists for as long as the tenant does, not for as long as one
    lease is live. A retired tenant is refused by the same
    refuse_write_to_retired_tenant trigger every insert into this table
    already goes through, so a retired tenant cannot mint a fresh one, but an
    already-minted identity for a tenant retired afterward is not itself
    revoked here; desired_document's emission for it is unconditional on
    ended_at, matching how a lease-derived identity's own ended_at is never
    set by anything today either. Withdrawing an already-issued ingest
    identity on retirement, if ever wanted, is a separate change.
    """
    import uuid

    from crypto import EnvelopeCrypto

    crypto = EnvelopeCrypto()
    row = db.one(
        "select identity_name, access_key_id, secret_ciphertext, "
        "secret_wrapped_key from storage_identity "
        "where tenant_id = %s and lease_id is null and agent_run_id is null "
        "and backend = 'seaweedfs'",
        (tenant_id,),
    )
    if row:
        secret = crypto.open(
            tenant_id, row["identity_name"],
            bytes(row["secret_ciphertext"]), bytes(row["secret_wrapped_key"]),
        )
        return {"identity_name": row["identity_name"],
                "access_key": row["access_key_id"],
                "secret_key": secret.decode("utf-8")}

    name = f"ingest-{tenant_id}"
    access_key = f"ingest-{tenant_id}"
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    sealed = crypto.seal(tenant_id, name, secret.encode("utf-8"))
    db.execute(
        """insert into storage_identity
             (id, tenant_id, identity_name, access_key_id,
              secret_ciphertext, secret_wrapped_key)
           values (%s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), tenant_id, name, access_key,
         sealed.ciphertext, sealed.wrapped_key),
    )
    return {"identity_name": name, "access_key": access_key,
            "secret_key": secret}


def identity_for_lease(lease_id: str, tenant_id: str) -> dict:
    """This lease's own storage identity, created once and reused after.

    Created on first use rather than when the lease is approved, because a
    lease nobody exercises should not leave a key lying in the document waiting
    to be leaked.
    """
    import uuid

    from crypto import EnvelopeCrypto

    crypto = EnvelopeCrypto()
    row = db.one(
        "select identity_name, access_key_id, secret_ciphertext, "
        "secret_wrapped_key from storage_identity where lease_id = %s",
        (lease_id,),
    )
    if row:
        secret = crypto.open(
            tenant_id, row["identity_name"],
            bytes(row["secret_ciphertext"]), bytes(row["secret_wrapped_key"]),
        )
        return {"identity_name": row["identity_name"],
                "access_key": row["access_key_id"],
                "secret_key": secret.decode("utf-8")}

    name = f"lease-{lease_id[:12]}"
    access_key = f"lease-{lease_id[:12]}"
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    sealed = crypto.seal(tenant_id, name, secret.encode("utf-8"))
    db.execute(
        """insert into storage_identity
             (id, tenant_id, lease_id, identity_name, access_key_id,
              secret_ciphertext, secret_wrapped_key)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), tenant_id, lease_id, name, access_key,
         sealed.ciphertext, sealed.wrapped_key),
    )
    return {"identity_name": name, "access_key": access_key,
            "secret_key": secret}


# Two reconciles must never interleave: each compiles the whole document and
# writes it, and an older compile written after a newer one would undo it.
# The API is one process, so a thread lock orders its own callers; the
# advisory lock orders it against the separate processes that also reconcile
# (reconcile-grants.py, remove-orphaned-files.py). The advisory lock is taken
# on a connection of its own rather than from the pool, so a caller waiting
# for it can never hold the pool connection the lock holder needs. Both waits
# are bounded: a hang is worse than an error.
_THREAD_LOCK = threading.Lock()
_ADVISORY_LOCK_ID = 846_152_907_331  # this document's lock, arbitrary but fixed
LOCK_WAIT_SECONDS = 30
# A print made inside somebody's request waits far less. A print takes about
# 0.1 s, so 5 s only runs out when something is wrong, and the caller must get
# its answer (a 202, which the activator follows up) well inside its own
# 20-second timeout rather than timing out and reading that as a crash.
REQUEST_LOCK_WAIT_SECONDS = 5


@contextmanager
def _document_lock(wait: int = LOCK_WAIT_SECONDS):
    import psycopg

    if not _THREAD_LOCK.acquire(timeout=wait):
        raise LockUnavailable(
            f"another reconcile in this process held the document for over "
            f"{wait}s. Refusing rather than waiting indefinitely"
        )
    try:
        with psycopg.connect(config.PG_DSN, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f"set lock_timeout = '{int(wait)}s'")
            try:
                conn.execute("select pg_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
            except psycopg.errors.LockNotAvailable as exc:
                raise LockUnavailable(
                    f"another process held the document lock for over "
                    f"{wait}s. Refusing rather than waiting indefinitely"
                ) from exc
            try:
                yield
            finally:
                conn.execute("select pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))
    finally:
        _THREAD_LOCK.release()


def reconcile(*, armed: bool = True, trigger: str = "manual") -> dict:
    """Compute the document and write it, or refuse and say why.

    `armed` exists for the first run. 224 leases expired without ever being
    withdrawn, so the first print legitimately removes close to half of the
    lease-derived grants, which is exactly the shape the guard is meant to
    catch. It is disarmed once, deliberately, by a person who has read the
    numbers, and armed everywhere else.
    """
    wait = (REQUEST_LOCK_WAIT_SECONDS if trigger in ("request", "provision")
            else LOCK_WAIT_SECONDS)
    try:
        with _document_lock(wait):
            print_id = _start_print(trigger)
            try:
                result = _reconcile(armed)
            except UnsafeProjection as exc:
                _finish_print(print_id, reason=str(exc), retryable=exc.retryable)
                raise
            except seaweed.StorageUnavailable as exc:
                _finish_print(print_id, reason=str(exc), retryable=True)
                raise
            except Exception as exc:
                _finish_print(print_id, reason=f"{type(exc).__name__}: {exc}",
                              retryable=True)
                raise
            _finish_print(print_id, identities=result["identities"],
                          auto_explained=result.get("auto_explained"))
            return result
    except LockUnavailable as exc:
        # Never got as far as starting a print, so record the attempt here.
        _finish_print(_start_print(trigger), reason=str(exc), retryable=True)
        raise


def _reconcile(armed: bool) -> dict:
    # The live document is read only to measure the change for the guard
    # below, never as an input to what is written.
    live = seaweed.load_identities()
    want = desired_document()

    before = sum(len(i.get("actions", [])) for i in live.get("identities", []))
    after = sum(len(i["actions"]) for i in want["identities"])

    structural = sum(
        1 for i in want["identities"] for a in i["actions"] if "/" not in a
    )
    if structural == 0:
        raise UnsafeProjection(
            "the computed document has no bucket-wide grants at all, so "
            "nothing could read or write anything. Refusing to write it"
        )

    live_prefix = sum(
        1 for i in live.get("identities", []) for a in i.get("actions", []) if "/" in a
    )
    want_prefix = sum(
        1 for i in want["identities"] for a in i["actions"] if "/" in a
    )
    auto_explained = None
    if armed and live_prefix and (live_prefix - want_prefix) / live_prefix > MAX_SHRINK:
        # Before refusing outright: how much of this shrink is routine lease
        # lifecycle, rather than something the guard actually exists to
        # catch? Recompute as of the last successful print -- a lease
        # counts as valid if it had not yet expired or been revoked then,
        # even if it has since. Bounded to that moment, not "ever": `live`
        # already reflects every lease that had already lapsed before that
        # print, so an unbounded "pretend nothing has ever expired" would
        # resurrect the platform's entire lease history, not just what
        # changed since. Never written; measured only, and thrown away
        # either way.
        as_of = last_success_started_at()
        lenient = desired_document(as_of=as_of) if as_of else want
        lenient_prefix = sum(
            1 for i in lenient["identities"] for a in i["actions"] if "/" in a
        )
        unexplained = live_prefix - lenient_prefix
        unexplained_shrink = unexplained / live_prefix
        if unexplained_shrink > MAX_SHRINK:
            raise UnsafeProjection(
                f"this print would remove {live_prefix - want_prefix} of "
                f"{live_prefix} prefix grants, more than the {MAX_SHRINK:.0%} a "
                f"routine reconcile should ever remove. Refusing to write it"
            )
        # The gap between the strict and lenient counts is exactly the part
        # of the shrink that traces to a lease that has since expired or
        # been revoked; what's left (`unexplained`) is comfortably within
        # the routine ceiling on its own, so the real (strict) document
        # proceeds without a person's approval.
        auto_explained = (
            f"this print removes {live_prefix - want_prefix} of {live_prefix} "
            f"prefix grants, over the {MAX_SHRINK:.0%} routine ceiling on its "
            f"own; {live_prefix - want_prefix - unexplained} of those trace to "
            f"leases that have since expired or been revoked, which alone "
            f"would explain a print recomputed as if they had not "
            f"(removing {unexplained} of {live_prefix}, {unexplained_shrink:.1%}, "
            f"within the ceiling), so this proceeded without a person's approval"
        )

    seaweed.save_identities(want)
    return {"identities": len(want["identities"]), "actions_before": before,
            "actions_after": after, "removed": before - after,
            "auto_explained": auto_explained}


# ------------------------------------------------------ print history --
#
# Approved is not the same as active.
# Every print is recorded in storage_permission_print; whether an allowed
# decision is in effect, how long printing has been failing and when to try
# again are all read from those rows, never kept as a second copy.

# The activator's retry spacing: 10 s after the first failure, doubling, never
# more than 5 minutes apart, and never giving up.
FIRST_RETRY_SECONDS = 10
MAX_RETRY_SECONDS = 300
# How long printing may fail before it is an administrator's problem.
ALERT_AFTER_SECONDS = 300


def _start_print(trigger: str) -> int:
    return db.execute(
        "insert into storage_permission_print (trigger) values (%s) returning id",
        (trigger,),
    )["id"]


def _finish_print(print_id: int, *, reason: str | None = None,
                  retryable: bool | None = None, identities: int | None = None,
                  auto_explained: str | None = None) -> None:
    db.execute(
        """update storage_permission_print
              set finished_at = clock_timestamp(), succeeded = %s,
                  retryable = %s, reason = %s, identities = %s,
                  auto_explained = %s
            where id = %s""",
        (reason is None, None if reason is None else retryable, reason, identities,
         auto_explained, print_id),
    )


def ingest_is_active(tenant_id: str) -> bool:
    """Whether this organisation's upload key already opens its bucket.

    Both the key and the bucket are rows in the register, so the key works
    once a successful print started after the later of the two was created.
    """
    row = db.one(
        """select greatest(i.created_at, p.created_at) as since
             from storage_identity i
             join tenant_storage_provision p
               on p.tenant_id = i.tenant_id and p.backend = 'seaweedfs'
            where i.tenant_id = %s and i.lease_id is null
              and i.agent_run_id is null and i.backend = 'seaweedfs'""",
        (tenant_id,),
    )
    return bool(row) and is_active(row["since"])


def last_success_started_at():
    """When the newest successful print began, or None if none has."""
    row = db.one("select max(started_at) as t from storage_permission_print where succeeded")
    return row["t"] if row else None


def is_active(since) -> bool:
    """Whether a successful print started after `since`, so that everything in
    the register at `since` is in the document."""
    started = last_success_started_at()
    return started is not None and started > since


def retry_delay_seconds(consecutive_failures: int) -> int:
    """The wait before the next automatic attempt after this many failures in
    a row: 10 s, 20 s, 40 s and so on, capped at 5 minutes."""
    if consecutive_failures <= 0:
        return 0
    return min(FIRST_RETRY_SECONDS * 2 ** (consecutive_failures - 1), MAX_RETRY_SECONDS)


def needs_alert(retryable: bool, failing_since, now) -> bool:
    """Whether an administrator needs telling about a failure that started at
    `failing_since`: at once if retrying cannot fix it, otherwise once it has
    lasted ALERT_AFTER_SECONDS."""
    from datetime import timedelta
    return (not retryable) or now - failing_since > timedelta(seconds=ALERT_AFTER_SECONDS)


def activation_status() -> dict:
    """Where printing stands, for the activator, /health and the console.

    `failing` is true when the newest print failed. `failing_since` is when the
    first failure after the last success started. `retry_due_at` is when the
    activator will try again, or None when it will not because the failure is
    one only a person can fix. `alert` is whether an administrator needs to
    know: printing has failed for longer than ALERT_AFTER_SECONDS, or failed in
    a way retrying cannot fix. `unserved` counts what is waiting on a print
    that has not happened yet: allowed decisions, parked runs and ended leases newer
    than the last successful print.
    """
    from datetime import datetime, timedelta, timezone

    last_ok = db.one(
        """select started_at, finished_at from storage_permission_print
            where succeeded order by started_at desc limit 1""")
    since = last_ok["started_at"] if last_ok else None
    failures = db.all_rows(
        """select started_at, finished_at, retryable, reason
             from storage_permission_print
            where succeeded = false and (%s::timestamptz is null or started_at > %s)
            order by started_at""", (since, since))
    latest = failures[-1] if failures else None
    # Only SeaweedFS grants wait on a print. R2 mints a credential that works
    # at once, so its decisions are never pending.
    pending = db.one(
        """select count(*) as n from access_decision d
             join dataset_version v on v.id = d.dataset_version_id
            where d.phase = 'grant' and d.allowed and v.storage_backend = 'seaweedfs'
              and (%s::timestamptz is null or d.at >= %s)""", (since, since))["n"]
    parked = db.one(
        """select count(*) as n,
                  count(*) filter (where %s::timestamptz is null
                                      or awaiting_activation_since >= %s) as unserved
             from agent_run where status = 'awaiting_activation'""", (since, since))
    # A lease that has ended (revoked, or its time passed) since the last print
    # still has its key in the live document. Nothing would print for it: a
    # request prints for itself, and this loop prints for failures. So it is
    # counted here, and the next tick drops the key. Without this a revoked or
    # expired lease's key worked until some unrelated request printed.
    ended = db.one(
        """select count(distinct l.id) as n
             from access_lease l
             join storage_identity si on si.lease_id = l.id and si.ended_at is null
            where (l.revoked and (%s::timestamptz is null or l.revoked_at >= %s))
               or (not l.revoked and l.expires_at is not null and l.expires_at <= now()
                   and (%s::timestamptz is null or l.expires_at >= %s))""",
        (since, since, since, since))["n"]
    # The catalog's rolling keys. A new epoch needs a new document (the keys
    # for the next one, and without the oldest), and a key resting on a lease
    # that has ended needs the document rewritten without it. Neither is asked
    # for by a request, so this loop does it.
    rotation = 0
    if last_ok:
        printed_epoch = catalog_epoch(last_ok["started_at"].timestamp())
        if catalog_epoch() != printed_epoch:
            rotation = db.one(
                "select count(*) as n from catalog_key where issued_epoch + 1 >= %s",
                (printed_epoch,))["n"]
    ended_keys = db.one(
        """select count(*) as n
             from catalog_key ck join access_lease l on l.id = ck.lease_id
            where (l.revoked and (%s::timestamptz is null or l.revoked_at >= %s))
               or (not l.revoked and l.expires_at is not null and l.expires_at <= now()
                   and (%s::timestamptz is null or l.expires_at >= %s))""",
        (since, since, since, since))["n"]
    retry_due = None
    if latest and latest["retryable"]:
        retry_due = latest["finished_at"] + timedelta(seconds=retry_delay_seconds(len(failures)))
    alert = bool(latest) and needs_alert(
        latest["retryable"], failures[0]["started_at"], datetime.now(timezone.utc))
    return {
        "last_success_at": last_ok["finished_at"] if last_ok else None,
        "failing": latest is not None,
        "failing_since": failures[0]["started_at"] if failures else None,
        "consecutive_failures": len(failures),
        "retryable": latest["retryable"] if latest else None,
        "reason": latest["reason"] if latest else None,
        "retry_due_at": retry_due,
        "alert": alert,
        "pending_decisions": pending,
        "ended_leases": ended,
        "parked_runs": parked["n"],
        "rotation_due": rotation,
        "unserved": pending + parked["unserved"] + ended + ended_keys + (1 if rotation else 0),
    }
