"""The record chain: every oversight record of an agent is written through here, so that a change made to one afterwards can be seen.

A record is inserted and, in the same transaction, a row is added to the agent's chain: the hash of the record as the database stored it, the hash of the chain row before, and a hash of both.
Checking recomputes all of it from what the database holds now. A record that was edited no longer matches its hash, a record that was removed leaves a chain row with nothing behind it,
a record added outside the platform has no chain row, and a chain row that was altered breaks every row after it.

What this shows and what it cannot: it shows that records were changed or removed. Somebody who can rewrite the whole chain could rewrite it consistently, which is why the head of the chain is
written into each evidence pack: a pack kept elsewhere is the check on the chain here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone

from . import db

GENESIS = "0" * 64

# What each kind of record is called in the chain, and the table that holds it.
KINDS = {
    "risk_record": "agent_risk_record",
    "change_approval": "agent_change_approval",
    "change_decision": "agent_change_decision",
    "output_review": "agent_output_review",
    "suspension": "agent_suspension",
    "incident": "agent_incident",
    "sponsorship": "agent_sponsorship",
    "reconfirmation": "agent_reconfirmation",
}

# The column that ties a table's rows to an agent, for finding records the chain does not know about. A decision belongs to an agent through its approval.
_AGENT_QUERY = {
    "agent_risk_record": "select id, {when} as at from agent_risk_record where agent_id = %s",
    "agent_change_approval": "select id, created_at as at from agent_change_approval where agent_id = %s",
    "agent_change_decision": "select d.id, d.decided_at as at from agent_change_decision d join agent_change_approval a on a.id = d.approval_id where a.agent_id = %s",
    "agent_output_review": "select id, reviewed_at as at from agent_output_review where agent_id = %s",
    "agent_suspension": "select id, acted_at as at from agent_suspension where agent_id = %s",
    "agent_incident": "select id, reported_at as at from agent_incident where agent_id = %s",
    "agent_sponsorship": "select id, assigned_at as at from agent_sponsorship where agent_id = %s",
    "agent_reconfirmation": "select id, confirmed_at as at from agent_reconfirmation where agent_id = %s",
}
_AGENT_QUERY["agent_risk_record"] = _AGENT_QUERY["agent_risk_record"].format(when="recorded_at")


def _plain(value):
    """A value as it is written into the hash, the same whenever it is read back."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def record_hash(kind: str, row: dict) -> str:
    """The hash of a record: its kind and every column it holds."""
    body = json.dumps({"kind": kind, "row": {k: _plain(v) for k, v in row.items()}}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def link_hash(prev_hash: str, this_record_hash: str) -> str:
    return hashlib.sha256((prev_hash + this_record_hash).encode("utf-8")).hexdigest()


def insert(cur, kind: str, agent_id: str, tenant_id: str, values: dict) -> dict:
    """Insert a record and add it to the agent's chain, in the caller's transaction. Returns the row as the database stored it."""
    table = KINDS[kind]
    cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (str(agent_id),))
    columns = list(values)
    marks = ", ".join(["%s"] * len(columns))
    cur.execute(f"insert into {table} ({', '.join(columns)}) values ({marks}) returning *", tuple(values[c] for c in columns))
    row = cur.fetchone()
    cur.execute("select chain_hash from agent_record_chain where agent_id = %s order by seq desc limit 1", (str(agent_id),))
    last = cur.fetchone()
    prev = last["chain_hash"] if last else GENESIS
    rh = record_hash(kind, row)
    cur.execute(
        "insert into agent_record_chain (tenant_id, agent_id, kind, record_id, record_hash, prev_hash, chain_hash) values (%s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, str(agent_id), kind, str(row["id"]), rh, prev, link_hash(prev, rh)))
    return row


def head(agent_id: str) -> dict:
    """The newest chain hash of an agent, and how many records are in the chain."""
    row = db.one("select count(*) as n, (select chain_hash from agent_record_chain where agent_id = %s order by seq desc limit 1) as head from agent_record_chain where agent_id = %s",
                 (str(agent_id), str(agent_id)))
    return {"records": row["n"], "head": row["head"] or GENESIS}


def verify(agent_id: str) -> dict:
    """Recompute the agent's chain from what the database holds now. `problems` is empty when nothing was changed, removed or added outside the platform."""
    chain = db.all_rows("select * from agent_record_chain where agent_id = %s order by seq", (str(agent_id),))
    problems: list[dict] = []
    prev = GENESIS
    for link in chain:
        where = {"seq": link["seq"], "kind": link["kind"], "record_id": str(link["record_id"])}
        if link["prev_hash"] != prev:
            problems.append({**where, "what": "This link does not follow the one before it: the chain was altered here."})
        table = KINDS.get(link["kind"])
        row = db.one(f"select * from {table} where id = %s", (str(link["record_id"]),)) if table else None
        if table is None:
            problems.append({**where, "what": "A kind of record this platform does not know."})
        elif row is None:
            problems.append({**where, "what": "The record that this link covers is gone."})
        elif record_hash(link["kind"], row) != link["record_hash"]:
            problems.append({**where, "what": "The record no longer says what it said when it was written."})
        if link_hash(link["prev_hash"], link["record_hash"]) != link["chain_hash"]:
            problems.append({**where, "what": "The hash of this link does not match what it covers."})
        prev = link["chain_hash"]

    in_chain = {(c["kind"], str(c["record_id"])) for c in chain}
    started = chain[0]["added_at"] if chain else None
    before_chain = 0
    for kind, table in KINDS.items():
        for r in db.all_rows(_AGENT_QUERY[table], (str(agent_id),)):
            if (kind, str(r["id"])) in in_chain:
                continue
            if started is not None and r["at"] >= started:
                problems.append({"seq": None, "kind": kind, "record_id": str(r["id"]), "what": "A record was added outside the platform: it has no link in the chain."})
            else:
                before_chain += 1
    return {"ok": not problems, "checked": len(chain), "head": prev, "problems": problems, "before_chain": before_chain}
