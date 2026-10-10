"""What a person can get out of a version through storage, worked out once, for every screen and route that has to say so.

A version can hold locked content: files, each locked under the key of the person it is about, or a table with locked columns. Storage holds that
content only as bytes nobody outside the platform can read. So permission to read "the whole version" (a storage credential) is worth different things
on different versions:

  files        every file is locked: the credential reads nothing that can be understood. What a person can have is a few files, opened by the platform
               at its own door (a lease on files);
  columns      some columns are locked: the credential reads the other columns, and the locked ones come back unreadable. A few rows of the locked
               columns are had through the platform (a lease on locked columns);
  all          nothing is locked: the credential reads everything, as it always did.

This is looked up from what was written when the version was sealed (the register of locked files and the privacy contract the version was sealed
under), never typed by a person and never guessed by a screen. The version page, the request routes, the approval, the start of an agent and the access
marks all ask this and use these sentences, so they cannot disagree.
"""

from __future__ import annotations

from . import db, privacy_contract

FILES = "files"
COLUMNS = "columns"
PLAIN = "plain"


def of(version_id: str) -> dict | None:
    """What this version holds that is locked, and what storage can give of it. None when there is no such version.

    shape         "files", "columns" or "plain" (see the module note);
    reads         what a whole-version permission reads: "all", "readable_columns" or "nothing";
    locked_files  how many locked files the version holds;
    locked_columns, readable_columns   how many columns are locked, and how many are not, for a table with locked columns.
    """
    version = db.one("select id, tenant_id, schema_id from dataset_version where id = %s", (version_id,))
    if not version:
        return None
    files = db.one("select count(*) as n from file_person where dataset_version_id = %s", (version_id,))["n"]
    if files:
        return {"shape": FILES, "reads": "nothing", "locked_files": files, "locked_columns": 0, "readable_columns": 0}
    contract = db.one("select document from privacy_contract where tenant_id = %s and schema_id = %s", (version["tenant_id"], version["schema_id"]))
    if contract:
        columns = privacy_contract.columns(contract["document"])
        locked = sum(1 for c in columns if c["treatment"] == "lock")
        if locked:
            return {"shape": COLUMNS, "reads": "readable_columns" if locked < len(columns) else "nothing", "locked_files": 0,
                    "locked_columns": locked, "readable_columns": len(columns) - locked}
    return {"shape": PLAIN, "reads": "all", "locked_files": 0, "locked_columns": 0, "readable_columns": 0}


def says(found: dict) -> str | None:
    """One sentence, for the person looking at the version, on what is locked and what can be asked for. None when nothing is locked."""
    if found["shape"] == FILES:
        return "These files are locked, each under the key of the person it is about, so the version cannot be handed over whole. You can ask to open a few of them."
    if found["shape"] == COLUMNS:
        if found["reads"] == "nothing":
            return "Every column of this table is locked, so the table cannot be handed over. You can ask to read a few rows of it."
        return ("This table has locked columns. You can ask to read its other columns, which are readable. The locked columns stay locked, "
                "and you can ask separately to read a few of their rows.")
    return None


def gives(found: dict) -> str | None:
    """What approving a request for the whole version would give, when that is less than the whole version. None when it gives everything."""
    if found["reads"] == "readable_columns":
        return "Approving this gives the readable columns only. The locked columns stay locked, and a few of their rows are asked for separately."
    if found["reads"] == "nothing":
        what = "every file in this version is locked" if found["shape"] == FILES else "every column of this table is locked"
        return f"Approving this is refused: {what}, so it would give nothing that can be read. Reject it and say to ask to open a few of them on the version's page."
    return None


def refusal_of_whole_version(found: dict, for_agent: bool = False) -> list[str]:
    """The reasons a request to read the whole version is refused, or an empty list when it can be granted and is worth something.

    Only a version with nothing readable is refused: where some of it is readable the request is allowed, and the screens say what it gives. An agent
    is told the fact and not the next step, because it has no page to ask on.
    """
    if found["reads"] != "nothing":
        return []
    what = "every file in this version is locked" if found["shape"] == FILES else "every column of this table is locked"
    if for_agent:
        return [f"{what}, so permission to read the whole version would give the agent nothing that can be read"]
    step = "To work with a few of the files, ask to open them on the version's page" if found["shape"] == FILES else "To read a few rows, ask for them on the version's page"
    return [f"{what}, so permission to read the whole version would give nothing that can be read. {step}"]
