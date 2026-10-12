"""Words for who may do something with an agent, built from the policy so that a sentence never names a role the policy does not name.

The officer's part of an agent change, resuming an agent whose level asks for a second approver, assigning a sponsor and re-confirming an agent are given by the roles in the policy's
`agent_officer_roles`; writing the risk levels and confirming an agent's level are given by `manage_risk_levels_roles` and `confirm_risk_level_roles`. A sentence that says who does
one of these reads that set and the role table, so a role added to a set appears in every such sentence without an edit here.
"""

from __future__ import annotations

import time

from . import opa

_CACHE: dict = {}
_SECONDS = 20.0   # the policy changes rarely, and a sentence that is twenty seconds old is not wrong


def words_for(set_name: str) -> str:
    """The roles in one of the policy's role sets, in words: "the AI risk owner", or "the data protection officer or the AI risk owner" for a set of two."""
    now = time.monotonic()
    cached = _CACHE.get(set_name)
    if cached and now - cached[0] < _SECONDS:
        return cached[1]
    roles = set(opa.read_document(f"munitas/access/{set_name}") or [])
    words = opa.role_words()
    order = opa.read_document("munitas/role_words/order") or list(words)
    names = [words[r].get("many") or words[r]["label"].lower() for r in order if r in roles and r in words]
    if not names:
        names = ["the officer"]
    text = names[0] if len(names) == 1 else (" or ".join(names) if len(names) == 2 else ", ".join(names[:-1]) + " or " + names[-1])
    _CACHE[set_name] = (now, text)
    return text


def officer_words() -> str:
    """The roles that act as the officer for an agent, in words."""
    return words_for("agent_officer_roles")


def sentence_start(words: str) -> str:
    """The words at the start of a sentence."""
    return words[:1].upper() + words[1:]


def officer_role_of(roles: list[str] | None) -> str:
    """The role a person acted as when they gave the officer's part, in words: the first role they hold that the policy lets act as the officer. A person who no longer holds one is spoken of by the role they held then, because the duty may have moved since."""
    held = set(roles or [])
    officer = set(opa.read_document("munitas/access/agent_officer_roles") or [])
    words = opa.role_words()
    order = opa.read_document("munitas/role_words/order") or list(words)
    for role in order:
        if role in held and role in officer and role in words:
            return words[role].get("many") or words[role]["label"].lower()
    # Nobody who gave a decision as the officer holds a role that does so now, because the duty has since moved to another role. The decision was given in the capacity of the role the person held
    # then, so say that one and not the role that holds the duty now.
    for role in order:
        if role in held and role in words and words[role].get("person"):
            return words[role].get("many") or words[role]["label"].lower()
    return officer_words()
