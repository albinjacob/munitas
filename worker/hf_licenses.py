"""What a HuggingFace licence tag actually permits, as a lookup rather than a
judgement call.

Duplicated from `platform/api/app/licenses.py` rather than shared: the API
and the worker are two separate Python processes with no shared package
between them (confirmed by how `worker/contracts.py` already defines its
own schema contracts independently of anything in the API). Keep the two in
sync by hand if the table grows; it is a dict, not logic, so the risk of
drift is a missed entry, not a wrong answer.
"""

from __future__ import annotations

from typing import NamedTuple


class LicenseTerms(NamedTuple):
    export_unmodified: bool
    export_modified: bool


HUGGINGFACE_LICENSES: dict[str, LicenseTerms] = {
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
    "cc-by-nd-4.0": LicenseTerms(True, False),
    "cc-by-nc-nd-4.0": LicenseTerms(False, False),
    "cc-by-nc-4.0": LicenseTerms(False, False),
    "cc-by-nc-sa-4.0": LicenseTerms(False, False),
    "cc-by-nc-sa-3.0": LicenseTerms(False, False),
}


def lookup(tag: str | None) -> LicenseTerms:
    if not tag:
        return LicenseTerms(False, False)
    return HUGGINGFACE_LICENSES.get(tag.lower(), LicenseTerms(False, False))
