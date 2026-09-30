"""What a HuggingFace licence tag actually permits, as a lookup rather than a
judgement call.

HuggingFace's `cardData.license` field is drawn from a small, controlled
vocabulary, not free text. That makes this a table, not a classifier: the same
reasoning that keeps `class_order`/`role_floor` as static maps in
`access.rego` rather than logic.

Two aspects, because they diverge for real licences: whether a dataset may be
exported exactly as fetched, and whether it may be exported after this
platform has modified it (de-identified, filtered, relabelled). A
no-derivatives licence answers yes to the first and no to the second.

Conservative by construction. A licence that only says something about *use*,
never about *redistribution*, is not on this list at all: absence means
"nothing is known", not "assumed open", and `lookup` returns both aspects as
refused for anything it does not recognise.
"""

from __future__ import annotations

from typing import NamedTuple


class LicenseTerms(NamedTuple):
    export_unmodified: bool
    export_modified: bool


# Not exhaustive. Covers the Creative Commons and OSI tags HuggingFace uses
# most. Extending this later is editing a dict, not redesigning anything.
HUGGINGFACE_LICENSES: dict[str, LicenseTerms] = {
    # Open: no condition this platform cannot already satisfy. Attribution is
    # a downstream courtesy owed by whoever receives the export, not
    # something the export itself blocks on.
    "cc0-1.0": LicenseTerms(True, True),
    "pddl": LicenseTerms(True, True),
    "unlicense": LicenseTerms(True, True),
    "mit": LicenseTerms(True, True),
    "apache-2.0": LicenseTerms(True, True),
    "bsd-3-clause": LicenseTerms(True, True),
    "cc-by-4.0": LicenseTerms(True, True),
    "cc-by-3.0": LicenseTerms(True, True),
    "cc-by-sa-4.0": LicenseTerms(True, True),
    "cc-by-sa-3.0": LicenseTerms(True, True),
    "odc-by": LicenseTerms(True, True),
    "odbl": LicenseTerms(True, True),
    # No-derivatives: fine as received, not fine once this platform has
    # changed it.
    "cc-by-nd-4.0": LicenseTerms(True, False),
    "cc-by-nc-nd-4.0": LicenseTerms(False, False),  # NC alone already blocks both
    # Non-commercial and RAIL-family licences carry a downstream-use
    # condition (non-commercial only, no illegal use, ...) this platform has
    # no way to enforce on whoever receives an export. Neither aspect is
    # granted; the tag itself is still recorded so a human can see exactly
    # what was found.
    "cc-by-nc-4.0": LicenseTerms(False, False),
    "cc-by-nc-sa-4.0": LicenseTerms(False, False),
    "cc-by-nc-sa-3.0": LicenseTerms(False, False),
}


def lookup(tag: str | None) -> LicenseTerms:
    if not tag:
        return LicenseTerms(False, False)
    return HUGGINGFACE_LICENSES.get(tag.lower(), LicenseTerms(False, False))
