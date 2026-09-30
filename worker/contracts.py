"""Schema contracts, enforced before a version is sealed.

A contract asks and a validator enforces, and the difference matters because a
prompt cannot reject anything. This is the validator.

Every dataset action declares a source contract and a target contract. The target
is checked against the actual records before the output version is created, so a
version that violates its own contract cannot come into existence. Checking after
sealing would be useless: the version is immutable by then, and the only remedy
left is a tombstone.
"""

from __future__ import annotations

from dataclasses import dataclass


class ContractViolation(Exception):
    """The records do not match the contract the action declared.

    Deliberately fatal to the activity. A pipeline that logs a contract
    violation and carries on produces exactly the situation the contract exists
    to prevent: a dataset whose declared shape and real shape disagree, with
    nothing downstream aware of it.
    """


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    sensitivity: str = "none"
    required: bool = True

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "sensitivity": self.sensitivity,
            "added_by": "munitas-worker",
        }


@dataclass(frozen=True)
class Contract:
    name: str
    fields: tuple[FieldSpec, ...]
    primary_key: tuple[str, ...]

    def as_payload(self, tenant_id: str) -> dict:
        return {
            "tenant_id": tenant_id,
            "name": self.name,
            "fields": [f.as_dict() for f in self.fields],
            "primary_key": list(self.primary_key),
        }

    def validate(self, records: list[dict]) -> None:
        """Check records against this contract, reporting every failure at once.

        Reporting all violations rather than the first is deliberate. A pipeline
        author who fixes one field, reruns a twenty minute transcription, and
        then discovers the next violation learns to distrust the validator.
        """
        problems: list[str] = []
        by_type = {
            "string": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }

        seen_keys: set[tuple] = set()
        for index, record in enumerate(records):
            for field in self.fields:
                if field.name not in record:
                    if field.required:
                        problems.append(f"record {index}: missing required field {field.name!r}")
                    continue
                value = record[field.name]
                expected = by_type.get(field.type)
                if expected and not isinstance(value, expected):
                    problems.append(
                        f"record {index}: field {field.name!r} should be {field.type}, "
                        f"got {type(value).__name__}"
                    )

            key = tuple(record.get(k) for k in self.primary_key)
            if any(part is None for part in key):
                problems.append(f"record {index}: primary key {self.primary_key} is incomplete")
            elif key in seen_keys:
                problems.append(f"record {index}: duplicate primary key {key}")
            else:
                seen_keys.add(key)

        if problems:
            shown = "\n  ".join(problems[:20])
            more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
            raise ContractViolation(
                f"{len(problems)} violations of contract {self.name!r}:\n  {shown}{more}"
            )


# The pipeline's contracts. Sensitivity is declared per field rather than per
# dataset, because a transcript and its record id do not carry the same risk and
# treating them alike is how over-restriction starts.

RAW_AUDIO = Contract(
    name="encounter_raw",
    fields=(
        FieldSpec("record_id", "string"),
        FieldSpec("audio_key", "string"),
        FieldSpec("duration_seconds", "float"),
        FieldSpec("sample_rate", "int"),
    ),
    primary_key=("record_id",),
)

TRANSCRIBED = Contract(
    name="encounter_transcribed",
    fields=(
        FieldSpec("record_id", "string"),
        FieldSpec("audio_key", "string"),
        FieldSpec("transcript", "string", sensitivity="phi"),
        FieldSpec("words", "list", sensitivity="phi"),
        FieldSpec("duration_seconds", "float"),
    ),
    primary_key=("record_id",),
)

DETECTED = Contract(
    name="encounter_detected",
    fields=(
        FieldSpec("record_id", "string"),
        FieldSpec("transcript", "string", sensitivity="phi"),
        FieldSpec("words", "list", sensitivity="phi"),
        FieldSpec("candidates", "list", sensitivity="phi"),
        FieldSpec("detector_votes", "dict"),
    ),
    primary_key=("record_id",),
)

REDACTED = Contract(
    name="encounter_redacted",
    fields=(
        FieldSpec("record_id", "string"),
        FieldSpec("redacted_transcript", "string", sensitivity="quasi"),
        FieldSpec("redacted_audio_key", "string", sensitivity="quasi"),
        FieldSpec("substitutions", "int"),
        FieldSpec("surrogate_map_stored", "bool"),
    ),
    primary_key=("record_id",),
)
