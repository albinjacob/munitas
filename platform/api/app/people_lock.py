"""Locking a batch of a large table about people.

A table of people too large to seal in one request is written by a table worker. The worker reads the file, checks every row, and then sends the rows
to be locked here, a few thousand at a time. This finds or makes each person's key, locks the cells that are to be locked under it, and gives the locked
cells and the person codes back. The worker writes them into the table and never holds a key: a key is opened here, used for the batch, and let go.

Everything is done in bulk. A person is found from many identifiers in one query, the keys of the people who are new are made and stored in one
statement, the keys of everybody in the batch are opened in one, and the key log gets one line for the batch that carries the count, where sealing
a small table writes a line for every person. A table of ten million people would otherwise write twenty million log lines to be read once.

Erasing a person is as it was: one key destroyed, one line in the log for that person, and the table not rewritten.
"""

from __future__ import annotations

import secrets

from crypto import KeyService, lock_value

from . import db, person_keys


class PersonGone(Exception):
    """A person in the batch had their key destroyed while the batch was being locked. Nothing is locked for the batch."""


def resolve_people(service: KeyService, tenant_id: str, kind: str, identifiers: list[str], actor: str, purpose: str) -> list[str]:
    """The person code for every identifier, in order, with a key made for each identifier not seen before. One code for a repeated identifier."""
    secret = service.lookup_secret(tenant_id)
    indexes = [person_keys.blind_index(secret, kind, value) for value in identifiers]
    distinct = list(dict.fromkeys(indexes))
    found = {r["blind_index"]: r["person_code"] for r in db.all_rows(
        "select blind_index, person_code from subject_lookup where tenant_id = %s and blind_index = any(%s)", (tenant_id, distinct))}
    missing = [i for i in distinct if i not in found]
    if missing:
        fresh = {index: "T-" + secrets.token_hex(8) for index in missing}
        service.make_person_keys(tenant_id, list(fresh.values()), actor, purpose)
        with db.cursor(commit=True) as cur:
            cur.execute("insert into subject_lookup (tenant_id, blind_index, person_code) select %s, i, c from unnest(%s::text[], %s::text[]) as t(i, c) "
                        "on conflict (tenant_id, blind_index) do nothing returning blind_index", (tenant_id, missing, [fresh[i] for i in missing]))
            won = {r["blind_index"] for r in cur.fetchall()}
        found.update({i: fresh[i] for i in won})
        lost = [i for i in missing if i not in won]
        if lost:
            # Somebody else made these people between the look and the insert. Theirs stand, and the keys made for this attempt go.
            for index in lost:
                service.destroy(tenant_id, fresh[index], actor)
            found.update({r["blind_index"]: r["person_code"] for r in db.all_rows(
                "select blind_index, person_code from subject_lookup where tenant_id = %s and blind_index = any(%s)", (tenant_id, lost))})
    return [found[i] for i in indexes]


def lock_rows(service: KeyService, *, tenant_id: str, kind: str, subjects: list[str], record_ids: list[str], columns: dict[str, list],
              actor: str, purpose: str) -> tuple[list[str], dict[str, list[str]]]:
    """The person codes of these rows and, for each named column, its cells locked under the key of the row's person.

    `subjects`, `record_ids` and every list in `columns` run together, row by row. A cell is bound to its person, its column and its row's id, so it
    cannot be moved to another and still open. Raises PersonGone when a person's key was destroyed meanwhile.
    """
    codes = resolve_people(service, tenant_id, kind, subjects, actor, purpose)
    keys = service.unwrap_batch(tenant_id, codes, actor, purpose)
    if any(key is None for key in keys.values()):
        raise PersonGone("a person in this batch was erased while it was being locked")
    locked = {name: [lock_value(keys[code], tenant_id, code, name, record_id, value) for code, record_id, value in zip(codes, record_ids, values)]
              for name, values in columns.items()}
    return codes, locked
