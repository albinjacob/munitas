"""The nine tables: what each column is, how a batch of rows is made, and the facts the contract states about it.

Health:    patients (about people), admissions (about people), diagnosis_codes (a lookup), ward_occupancy (large, composite key)
Finance:   customers (about people), transactions (about people), merchant_categories (a lookup), fx_ticks (large, composite key)
Vehicles:  vehicle_readings (large, no key)

Nothing here can belong to a real person: record numbers are a local scheme, emails use the domains reserved for documentation, and
phone and national numbers use ranges that are reserved for fiction or can never be issued.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from . import pools
from .hashing import Stream, cumulative

GENERATOR_VERSION = 1


def days(text: str) -> int:
    return (date.fromisoformat(text) - date(1970, 1, 1)).days


def seconds(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


TODAY = days("2026-09-30")
TODAY_END = seconds("2026-09-30T23:59:59")


@dataclass(frozen=True)
class Context:
    seed: int
    rows: int
    people: int


@dataclass(frozen=True)
class Col:
    name: str
    type: pa.DataType
    classification: str = "none"
    treatment: str = "readable"
    key: int = 0                      # position in the primary key, 1 first; 0 when not part of it
    person_identifier: bool = False
    reason: str = ""                  # why a sensitive column is left readable
    approved_by: str = ""
    nullable: bool = True
    fmt: str = ""                     # how a date or timestamp is written
    zone: bool | None = None          # whether a timestamp carries a time zone; None for other types
    note: str = ""


@dataclass(frozen=True)
class TableSpec:
    name: str
    domain: str
    kind: str                         # "people" or "plain"
    cols: tuple
    build: Callable[[int, int, Context], pa.Table]
    summary: str
    fixed_rows: int | None = None     # a lookup has the rows it has, whatever is asked for
    rows_per_person: int = 4          # how many rows each person has on average, for a table about people
    subject: dict | None = None
    not_erasable_reason: str = ""
    large: bool = False               # a table whose point is its size

    def schema(self) -> pa.Schema:
        return pa.schema([pa.field(c.name, c.type, nullable=c.nullable and not c.key) for c in self.cols])

    def schema_as_text(self) -> pa.Schema:
        return pa.schema([pa.field(c.name, pa.string()) for c in self.cols])

    def key_columns(self) -> list[str]:
        return [c.name for c in sorted((c for c in self.cols if c.key), key=lambda c: c.key)]

    def context(self, rows: int, seed: int) -> Context:
        rows = self.fixed_rows or rows
        return Context(seed=seed, rows=rows, people=max(1, rows // self.rows_per_person) if self.kind == "people" else 0)


# ---- small helpers ---------------------------------------------------------------------------------------------------------------


def _s(array) -> pa.Array:
    return pa.array(array, pa.string())


def number_string(values: np.ndarray, width: int, prefix: str = "") -> pa.Array:
    text = pc.utf8_lpad(pc.cast(pa.array(values.astype(np.int64)), pa.string()), width=width, padding="0")
    return pc.binary_join_element_wise(pa.scalar(prefix), text, "") if prefix else text


def record_number(index: np.ndarray, prefix: str) -> pa.Array:
    """A nine digit number that looks arbitrary and is different for every person: the person's number times a constant that shares no
    factor with a billion. It is a local scheme, so it can never be somebody's real number."""
    return number_string((index.astype(np.int64) * 654435761) % 10 ** 9, 9, prefix)


def timestamps(seconds_since_epoch: np.ndarray, unit: str = "s", mask: np.ndarray | None = None) -> pa.Array:
    values = pa.array(seconds_since_epoch.astype(np.int64), mask=mask)
    return values.cast(pa.timestamp(unit))


def dates(days_since_epoch: np.ndarray) -> pa.Array:
    return pa.array(days_since_epoch.astype(np.int32)).cast(pa.date32())


def decimal(units: np.ndarray, precision: int, scale: int, mask: np.ndarray | None = None) -> pa.Array:
    """A decimal column from whole numbers that already count the smallest unit (cents, millionths)."""
    units = units.astype(np.int64)
    n = len(units)
    words = np.empty((n, 2), dtype="<u8")
    words[:, 0] = units.view(np.uint64)
    words[:, 1] = np.where(units < 0, np.uint64(0xFFFFFFFFFFFFFFFF), np.uint64(0))
    out = pa.Array.from_buffers(pa.decimal128(precision, scale), n, [None, pa.py_buffer(words.tobytes())])
    return pc.if_else(pa.array(~mask), out, pa.scalar(None, out.type)) if mask is not None and mask.any() else out


def choose(array: list[str], index: np.ndarray, mask: np.ndarray | None = None) -> pa.Array:
    return pa.array(np.array(array, dtype=object)[index], pa.string(), mask=mask)


# ---- people ------------------------------------------------------------------------------------------------------------------


DOMAIN_SEED = {"health": 0, "finance": 1_000_003}


def person_core(index: np.ndarray, seed: int, domain: str) -> dict:
    """What is true of a person, a function of the person's number alone: where they are from, their sex and their date of birth."""
    s = Stream(index, seed + DOMAIN_SEED[domain])   # a patient and a customer with the same number are different people
    locale = s.pick("locale", cumulative(pools.LOCALE_WEIGHTS))
    sex_u = s.u("sex")
    sex = np.where(sex_u < 0.504, 0, np.where(sex_u < 0.996, 1, 2))
    bucket = s.u("age.bucket")
    if domain == "health":
        age = np.where(bucket < 0.2, s.between("age.child", 0, 17), np.where(bucket < 0.74, s.between("age.adult", 18, 64), s.between("age.old", 65, 94)))
    else:
        age = np.where(bucket < 0.7, s.between("age.adult", 18, 64), s.between("age.old", 65, 90))
    dob = TODAY - (age * 365.25).astype(np.int64) - s.below("age.days", 365)
    return {"locale": locale, "sex": sex, "dob": dob, "stream": s, "name_group": locale * 2 + np.where(sex == 1, 1, np.where(sex == 2, (s.u("sex.x") > 0.5).astype(np.int64), 0))}


def _per_locale(locale: np.ndarray, make) -> np.ndarray:
    """An array of text made one locale at a time: `make(locale_number, positions)` returns the strings for those positions."""
    out = np.empty(len(locale), dtype=object)
    for number in range(len(pools.LOCALES)):
        at = np.nonzero(locale == number)[0]
        if len(at):
            out[at] = make(number, at)
    return out


def _postcode(number: int, s: Stream, at: np.ndarray, prefix: list[str]) -> list[str]:
    d = [s.between(f"pc.{k}", 0, 9)[at] for k in range(6)]
    letters = np.array(list("ABDEFGHJLNPQRSTUWXYZ"))
    l1, l2 = letters[s.below("pc.l1", len(letters))[at]], letters[s.below("pc.l2", len(letters))[at]]
    if number == 0:   # a postcode of the UK pattern: area, one or two digits, a space, a digit and two letters
        return [f"{p}{max(1, a)}{b if (b % 3 == 0) else ''} {c}{x}{y}" for p, a, b, c, x, y in zip(prefix, d[0], d[1], d[2], l1, l2)]
    if number == 1:   # a five digit ZIP: the three digit prefix of the city, then two digits
        return [f"{p}{a}{b}" for p, a, b in zip(prefix, d[0], d[1])]
    if number == 2:   # an Eircode: routing key, then four characters
        return [f"{p}{(a * 10 + b) % 24 + 1:02d} {x}{y}{c}{e}" if p == "D" else f"{p}{a}{b} {x}{y}{c}{e}" for p, a, b, x, y, c, e in zip(prefix, d[0], d[1], l1, l2, d[2], d[3])]
    return [f"{p}{a}{b}{c}" for p, a, b, c in zip(prefix, d[0], d[1], d[2])]   # a six digit PIN


def person_text(index: np.ndarray, seed: int, domain: str, core: dict | None = None) -> dict:
    """The words about each person: name, address, town, postcode, phone and email."""
    core = core or person_core(index, seed, domain)
    s, locale, n = core["stream"], core["locale"], len(index)
    pool = pools.names()
    first, last, last2 = pool["first"].take(core["name_group"], s.u("first")), pool["last"].take(locale, s.u("last")), pool["last"].take(locale, s.u("last2"))
    last_full = pc.if_else(pa.array(s.chance("barrel", 0.02)), pc.binary_join_element_wise(last, last2, "-"), last)
    nickname = pc.binary_join_element_wise(first, ' "', pc.utf8_slice_codeunits(first, 0, 3), '" ', last_full, "")
    full_name = pc.if_else(pa.array(s.chance("nick", 0.0005)), nickname, pc.binary_join_element_wise(first, " ", last_full, ""))

    house = 1 + (250 * s.u("house") ** 2).astype(np.int64)
    street = pool["street"].take(locale, s.u("street"))
    line = pc.binary_join_element_wise(pc.cast(pa.array(house), pa.string()), street, " ")
    unit = s.below("unit", 30) + 1
    word = pa.array(np.where(locale == 1, "Apt", "Flat"))
    split = np.where(s.chance("unit.newline", 0.13), "\n", ", ")
    with_unit = pc.binary_join_element_wise(word, pc.cast(pa.array(unit), pa.string()), pa.array(split), line, " ")
    with_unit = pc.replace_substring(with_unit, " \n ", "\n")
    with_unit = pc.replace_substring(with_unit, " , ", ", ")
    address = pc.if_else(pa.array(s.chance("unit", 0.15)), with_unit, line)

    towns = pools.towns()
    pick = {}
    for number in range(len(pools.LOCALES)):
        pick[number] = np.searchsorted(towns[number]["cumulative"], s.u("town"), side="right").clip(0, len(towns[number]["prefix"]) - 1)
    town = _per_locale(locale, lambda k, at: np.array(towns[k]["town"].to_pylist(), dtype=object)[pick[k][at]])
    region = _per_locale(locale, lambda k, at: np.array(towns[k]["region"].to_pylist(), dtype=object)[pick[k][at]])
    postcode = _per_locale(locale, lambda k, at: np.array(_postcode(k, s, at, [towns[k]["prefix"][i] for i in pick[k][at]]), dtype=object))

    digits = [s.between(f"ph.{k}", 0, 9) for k in range(6)]
    phone = _per_locale(locale, lambda k, at: np.array([
        (f"+44 7700 900{a}{b}{c}", f"+1 202-555-01{a}{b}", f"+353 1 000 {a}{b}{c}{d}", f"+91 00000 {a}{b}{c}{d}{e}")[k]
        for a, b, c, d, e in zip(digits[0][at], digits[1][at], digits[2][at], digits[3][at], digits[4][at])], dtype=object))

    local = pc.utf8_lower(pc.binary_join_element_wise(first, ".", last_full, ""))
    local = pc.replace_substring_regex(local, "[^a-z0-9.]", "")
    suffix = pc.if_else(pa.array(s.chance("email.digits", 0.55)), pc.cast(pa.array(s.between("email.n", 1, 99)), pa.string()), "")
    domain_name = choose(["example.com", "example.org", "example.net"], s.pick("email.domain", cumulative([5, 3, 2])))
    email = pc.binary_join_element_wise(local, suffix, "@", domain_name, "")
    return {"first": first, "last": last_full, "full_name": full_name, "address_line": address, "town": _s(town), "region": _s(region),
            "postcode": _s(postcode), "phone": _s(phone), "email": email, "n": n}


def national_id(core: dict) -> pa.Array:
    """A national number that can never have been issued: the UK prefix QQ, a US area number of 900 or more, an Indian number that starts with 1."""
    s, locale = core["stream"], core["locale"]
    d = [s.between(f"nid.{k}", 0, 9) for k in range(12)]
    suffix = np.array(list("ABCD"))[s.below("nid.l", 4)]
    return _s(_per_locale(locale, lambda k, at: np.array([
        (f"QQ{a}{b}{c}{e}{f}{g}{x}", f"9{a}{b}-{c}{e}-{f}{g}{h}{i}", f"QQ{a}{b}{c}{e}{f}{g}{x}", f"1{a}{b}{c} {e}{f}{g}{h} {i}{j}{k_}{l}")[k]
        for a, b, c, e, f, g, h, i, j, k_, l, x in zip(d[0][at], d[1][at], d[2][at], d[3][at], d[4][at], d[5][at], d[6][at], d[7][at], d[8][at],
                                                       d[9][at], d[10][at], suffix[at])], dtype=object)))


# ---- health ------------------------------------------------------------------------------------------------------------------


def _patients(first: int, count: int, ctx: Context) -> pa.Table:
    index = np.arange(first, first + count, dtype=np.int64)
    core = person_core(index, ctx.seed, "health")
    text = person_text(index, ctx.seed, "health", core)
    s = core["stream"]
    registered = np.minimum(days("2015-01-01") + s.below("registered", TODAY - days("2015-01-01")), TODAY)
    registered = np.minimum(np.maximum(registered, core["dob"] + s.below("registered.after", 60)), TODAY)
    blood = choose(["O+", "A+", "B+", "O-", "A-", "AB+", "B-", "AB-"], s.pick("blood", cumulative([36, 30, 9, 13, 6, 3, 2, 1])))
    return pa.table({
        "registration_id": number_string(index + 1, 8, "REG"),
        "patient_number": record_number(index, "MRN"),
        "full_name": text["full_name"],
        "date_of_birth": dates(core["dob"]),
        "sex": choose(["F", "M", "X"], core["sex"]),
        "address_line": text["address_line"],
        "town": text["town"],
        "region": text["region"],
        "postcode": text["postcode"],
        "phone": text["phone"],
        "email": text["email"],
        "blood_group": blood,
        "registered_on": dates(registered),
    }, schema=SPECS["patients"].schema())


SITES = ["RH", "GH", "SM", "NW", "EL"]
SPECIALTIES = ["MED", "SUR", "ICU", "AAU", "MAT", "PAE", "ORT", "CAR", "ONC", "GER", "NEU", "REN", "RES", "GAS", "PSY", "REH"]
WARDS = [f"{SITES[w % 5]}-{SPECIALTIES[(w // 5) % 16]}-{(w // 80) + 1:02d}" for w in range(800)]
CHAPTER_WARD = {"A": "MED", "B": "MED", "C": "ONC", "D": "MED", "E": "MED", "F": "PSY", "G": "NEU", "H": "MED", "I": "CAR", "J": "RES", "K": "GAS",
                "L": "MED", "M": "ORT", "N": "REN", "O": "MAT", "P": "PAE", "Q": "PAE", "R": "AAU", "S": "ORT", "T": "SUR", "Z": "MED"}
CHAPTER_NAME = {"A": "Infectious and parasitic diseases", "B": "Infectious and parasitic diseases", "C": "Neoplasms", "D": "Blood and immune disorders",
                "E": "Endocrine, nutritional and metabolic", "F": "Mental and behavioural", "G": "Nervous system", "H": "Eye, ear and balance",
                "I": "Circulatory system", "J": "Respiratory system", "K": "Digestive system", "L": "Skin", "M": "Musculoskeletal system",
                "N": "Genitourinary system", "O": "Pregnancy and childbirth", "P": "Conditions of the newborn", "Q": "Congenital conditions",
                "R": "Symptoms and findings", "S": "Injury", "T": "Injury and poisoning", "Z": "Factors influencing health contact"}


def _day_weights() -> np.ndarray:
    span = np.arange(days("2020-01-01"), TODAY + 1)
    weekday = (span + 3) % 7                       # 0 is Monday: 1970-01-01 was a Thursday
    by_weekday = np.array([1.12, 1.10, 1.08, 1.06, 1.0, 0.82, 0.80])[weekday]
    as_days = span.astype("datetime64[D]")
    day_of_year = (as_days - as_days.astype("datetime64[Y]").astype("datetime64[D]")).astype(np.int64)
    winter = 1.0 + 0.25 * np.cos(2 * math.pi * (day_of_year - 15) / 365.0)
    quiet = np.where((span >= days("2020-03-23")) & (span <= days("2020-06-30")), 0.7, 1.0)   # the spring of 2020
    return cumulative(by_weekday * winter * quiet)


_DAY_CUM = None
_HOUR_CUM = cumulative([0.6, 0.4, 0.3, 0.3, 0.3, 0.4, 0.7, 1.2, 1.8, 2.4, 2.8, 2.9, 2.7, 2.5, 2.4, 2.5, 2.6, 2.7, 2.6, 2.3, 1.9, 1.5, 1.1, 0.8])


def _admission_pool():
    d = pools.diagnoses()
    codes = [x["code"] for x in d]
    kind = np.array(["newborn" if c in ("Z38.0", "P07.3", "P59.9", "Q21.0") else "obstetric" if c.startswith("O") else "male" if c in ("C61", "N40") else "general"
                     for c in codes])
    return {"codes": codes, "kind": kind, "cumulative": cumulative([x["weight"] for x in d]), "stay": np.array([x["mean_stay_days"] for x in d], dtype=float),
            "cost": np.array([x["cost_factor"] for x in d]), "chapter": np.array([c[0] for c in codes]), "fallback_adult": codes.index("Z03.9"),
            "fallback_newborn": codes.index("Z38.0")}


_ADM = None


def _admissions(first: int, count: int, ctx: Context) -> pa.Table:
    global _DAY_CUM, _ADM
    _DAY_CUM = _DAY_CUM if _DAY_CUM is not None else _day_weights()
    adm = _ADM = _ADM or _admission_pool()
    index = np.arange(first, first + count, dtype=np.int64)
    s = Stream(index, ctx.seed)
    person = np.minimum((ctx.people * s.u("person") ** 1.7).astype(np.int64), ctx.people - 1)
    core = person_core(person, ctx.seed, "health")
    dob_sec = core["dob"] * 86400

    when = (days("2020-01-01") + s.pick("day", _DAY_CUM)) * 86400 + s.pick("hour", _HOUR_CUM) * 3600 + s.below("second", 3600)
    before_birth = when < dob_sec
    when = np.where(before_birth, dob_sec + s.below("born", 86400 * 3), when)
    when = np.minimum(when, TODAY_END)
    age_days = (when - dob_sec) // 86400
    age_years = (age_days / 365.25).astype(np.int64)

    sex = core["sex"]
    chosen = np.full(count, -1, dtype=np.int64)
    for attempt in range(4):
        d = s.pick(f"dx.{attempt}", adm["cumulative"])
        kind = adm["kind"][d]
        fits = np.where(age_days <= 28, kind == "newborn", kind != "newborn")
        fits &= (kind != "obstetric") | ((sex == 0) & (age_years >= 16) & (age_years <= 48))
        fits &= (kind != "male") | (sex == 1)
        chosen = np.where((chosen < 0) & fits, d, chosen)
    chosen = np.where(chosen < 0, np.where(age_days <= 28, adm["fallback_newborn"], adm["fallback_adult"]), chosen)

    stay = np.floor(adm["stay"][chosen] * np.exp(0.55 * s.normal("stay"))).astype(np.int64).clip(0, 120)
    leave = when + stay * 86400 + np.where(stay == 0, 1800 + s.below("leave.day", 36000), s.below("leave", 86400))
    in_hospital = leave > TODAY_END

    cost = np.floor((350 + 420 * stay) * adm["cost"][chosen] * np.exp(0.22 * s.normal("cost"))) * 100 + s.below("cost.cents", 100)
    age = age_years
    severe = np.clip(adm["cost"][chosen] - 1.0, 0, None)
    outcome_u = s.u("outcome")
    died = 0.003 + 0.02 * severe + 0.01 * (age > 80)
    care = died + 0.02 + 0.10 * (age > 75)
    transferred = care + 0.045
    walked = transferred + 0.01
    outcome = np.where(outcome_u < died, 0, np.where(outcome_u < care, 1, np.where(outcome_u < transferred, 2, np.where(outcome_u < walked, 3, 4))))
    outcome_text = np.array(["died", "discharged to care home", "transferred", "self-discharged", "discharged home"], dtype=object)[outcome]
    outcome_text = np.where(in_hospital, "in hospital", outcome_text)

    chapter = adm["chapter"][chosen]
    mapped = np.array([CHAPTER_WARD.get(c, "MED") for c in chapter])
    off_pattern = s.u("ward.off")
    specialty = np.where(off_pattern < 0.70, mapped, np.where(off_pattern < 0.85, "MED", np.where(off_pattern < 0.93, "SUR", "AAU")))
    site = s.below("ward.site", 5)
    number = s.between("ward.number", 1, 10)
    spec_index = np.array([SPECIALTIES.index(x) for x in specialty])
    ward = np.array(WARDS, dtype=object)[site + 5 * spec_index + 80 * (number - 1)]

    return pa.table({
        "admission_id": number_string(index + 1, 10, "ADM"),
        "patient_number": record_number(person, "MRN"),
        "ward_code": _s(ward),
        "admitted_at": timestamps(when),
        "discharged_at": timestamps(leave, mask=in_hospital),
        "primary_diagnosis": choose(adm["codes"], chosen),
        "age_at_admission": pa.array(age_years),
        "length_of_stay_days": pa.array(stay, mask=in_hospital),
        "total_cost": decimal(cost, 12, 2, mask=in_hospital),
        "outcome": _s(outcome_text),
    }, schema=SPECS["admissions"].schema())


def _diagnosis_codes(first: int, count: int, ctx: Context) -> pa.Table:
    rows = pools.diagnoses()[first:first + count]
    return pa.table({
        "code": _s([r["code"] for r in rows]), "description": _s([r["description"] for r in rows]),
        "chapter": _s([CHAPTER_NAME[r["code"][0]] for r in rows]),
        "typical_stay_days": pa.array([r["mean_stay_days"] for r in rows], pa.int64()),
        "relative_cost": pa.array([r["cost_factor"] for r in rows], pa.float64()),
    }, schema=SPECS["diagnosis_codes"].schema())


def _ward_occupancy(first: int, count: int, ctx: Context) -> pa.Table:
    index = np.arange(first, first + count, dtype=np.int64)
    ward, hour = index % 800, index // 800
    w = Stream(ward, ctx.seed)
    open_beds = w.between("beds", 10, 36)
    s = Stream(index, ctx.seed)
    hour_of_day, day = hour % 24, hour // 24
    day_of_year = day % 365
    closed = Stream(ward * 1000003 + day, ctx.seed).chance("closed", 0.008)
    base = 0.55 + 0.4 * w.u("occupancy")
    fraction = np.clip(base + 0.06 * np.sin(2 * math.pi * (hour_of_day - 8) / 24) + 0.05 * np.cos(2 * math.pi * (day_of_year - 20) / 365) + 0.03 * s.normal("noise"), 0.05, 1.0)
    open_beds = np.where(closed, 0, open_beds)
    occupied = np.floor(open_beds * fraction + 0.5).astype(np.int64)
    arrivals = np.floor(open_beds * 0.02 * (1 + 0.8 * np.sin(2 * math.pi * (hour_of_day - 10) / 24)) + s.u("arrivals")).astype(np.int64).clip(0, None)
    leavers = np.floor(open_beds * 0.02 * (1 + 0.8 * np.sin(2 * math.pi * (hour_of_day - 14) / 24)) + s.u("leavers")).astype(np.int64).clip(0, None)
    return pa.table({
        "ward_code": pa.array(WARDS, pa.string()).take(pa.array(ward)),
        "hour_start": timestamps(seconds("2020-01-01T00:00:00") + hour * 3600),
        "beds_open": pa.array(open_beds),
        "beds_occupied": pa.array(occupied),
        "admissions_in_hour": pa.array(arrivals),
        "discharges_in_hour": pa.array(leavers),
    }, schema=SPECS["ward_occupancy"].schema())


# ---- finance -----------------------------------------------------------------------------------------------------------------


def _customers(first: int, count: int, ctx: Context) -> pa.Table:
    index = np.arange(first, first + count, dtype=np.int64)
    core = person_core(index, ctx.seed, "finance")
    text = person_text(index, ctx.seed, "finance", core)
    s = core["stream"]
    joined = np.minimum(np.maximum(days("2008-01-01") + s.below("joined", TODAY - days("2008-01-01")), core["dob"] + 18 * 365 + s.below("joined.age", 400)), TODAY)
    return pa.table({
        "customer_record": number_string(index + 1, 9, "CR"),
        "customer_number": record_number(index, "CUS"),
        "full_name": text["full_name"],
        "date_of_birth": dates(core["dob"]),
        "national_id": national_id(core),
        "address_line": text["address_line"],
        "town": text["town"],
        "postcode": text["postcode"],
        "phone": text["phone"],
        "email": text["email"],
        "segment": choose(["retail", "premium", "business"], s.pick("segment", cumulative([78, 14, 8]))),
        "risk_band": choose(["A", "B", "C", "D", "E"], s.pick("risk", cumulative([34, 31, 22, 9, 4]))),
        "joined_on": dates(joined),
    }, schema=SPECS["customers"].schema())


_MCC = None


def _mcc_pool():
    m = pools.merchant_categories()
    return {"codes": [x["mcc"] for x in m], "cumulative": cumulative([x["weight"] for x in m]), "median": np.array([x["median_amount"] for x in m]),
            "group": np.array([x["group"] for x in m])}


def _transactions(first: int, count: int, ctx: Context) -> pa.Table:
    global _MCC
    mcc = _MCC = _MCC or _mcc_pool()
    index = np.arange(first, first + count, dtype=np.int64)
    s = Stream(index, ctx.seed)
    customer = np.minimum((ctx.people * s.u("customer") ** 1.4).astype(np.int64), ctx.people - 1)
    core = person_core(customer, ctx.seed, "finance")
    category = s.pick("mcc", mcc["cumulative"])
    day = days("2023-01-01") + s.below("day", TODAY - days("2023-01-01") + 1)
    hour = s.pick("hour", cumulative([0.3, 0.2, 0.15, 0.15, 0.2, 0.4, 0.9, 1.6, 2.1, 2.2, 2.4, 2.8, 3.2, 2.9, 2.6, 2.5, 2.6, 2.9, 3.1, 2.8, 2.2, 1.6, 1.0, 0.6]))
    posted = day * 86400 + hour * 3600 + s.below("second", 3600)
    cents = np.maximum(50, np.round(mcc["median"][category] * np.exp(0.8 * s.normal("amount")) * 100)).astype(np.int64)
    codes = np.array(mcc["codes"])[category]
    cents = np.where(codes == "6011", (cents // 1000) * 1000, cents)        # a cash machine pays out in tens
    cents = np.where(s.chance("credit", 0.03), -cents, cents)
    home = np.array(["GBP", "USD", "EUR", "INR"], dtype=object)[core["locale"]]
    abroad = np.array(["EUR", "USD", "GBP", "JPY", "CHF", "AED"], dtype=object)[s.below("abroad", 6)]
    currency = np.where(s.chance("foreign", 0.06), abroad, home)
    online = s.chance("online", 0.33)
    channel = np.where(codes == "6011", "atm", np.where(online, "online", np.where(s.chance("contactless", 0.4), "contactless", "card present")))
    status_u = s.u("status")
    status = np.where(status_u < 0.972, "settled", np.where(status_u < 0.987, "pending", np.where(status_u < 0.998, "declined", "reversed")))
    merchant = np.array([f"M{c}-{n:03d}" for c, n in zip(codes, s.below("merchant", 400))], dtype=object)
    return pa.table({
        "transaction_id": number_string(index + 1, 11, "TX"),
        "customer_number": record_number(customer, "CUS"),
        "posted_at": timestamps(posted),
        "amount": decimal(cents, 14, 2),
        "currency": _s(currency),
        "merchant_id": _s(merchant),
        "merchant_category": choose(mcc["codes"], category),
        "channel": _s(channel),
        "status": _s(status),
    }, schema=SPECS["transactions"].schema())


def _merchant_categories(first: int, count: int, ctx: Context) -> pa.Table:
    rows = pools.merchant_categories()[first:first + count]
    return pa.table({
        "mcc": _s([r["mcc"] for r in rows]), "description": _s([r["description"] for r in rows]), "category_group": _s([r["group"] for r in rows]),
        "typical_amount": pa.array([r["median_amount"] for r in rows], pa.float64()),
    }, schema=SPECS["merchant_categories"].schema())


PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY"]
BASE = np.array([1.085, 1.27, 149.5, 0.88, 0.66, 1.36, 0.61, 0.855, 162.0, 190.0, 0.955, 98.7])
VENUES = ["EBS", "Refinitiv", "Currenex", "Hotspot"]


def _fx_ticks(first: int, count: int, ctx: Context) -> pa.Table:
    index = np.arange(first, first + count, dtype=np.int64)
    s = Stream(index, ctx.seed)
    pair, step = index % 12, index // 12
    t_ms = step * 250 + s.below("jitter", 200)
    t_days = step * 0.25 / 86400.0
    phase = pair * 0.7
    mid = BASE[pair] * (1 + 0.015 * np.sin(2 * math.pi * t_days / 9.3 + phase) + 0.006 * np.sin(2 * math.pi * t_days / 1.7 + 2 * phase)
                        + 0.00015 * s.normal("noise"))
    bid = np.round(mid * 1e6).astype(np.int64)
    spread = np.maximum(1, np.round(mid * 4e-5 * 1e6 * (0.8 + 0.4 * s.u("spread")))).astype(np.int64)
    return pa.table({
        "pair": choose(PAIRS, pair),
        "ticked_at": timestamps(seconds("2025-01-06T00:00:00") * 1000 + t_ms, unit="ms"),
        "bid": decimal(bid, 12, 6),
        "ask": decimal(bid + spread, 12, 6),
        "venue": choose(VENUES, s.pick("venue", cumulative([5, 3, 2, 1]))),
    }, schema=SPECS["fx_ticks"].schema())


# ---- vehicles ----------------------------------------------------------------------------------------------------------------

FAULTS = ["P0300", "P0171", "P0420", "P0442", "P0128", "P0301", "P0113", "C0035", "U0100", "P0455"]


def _vehicle_readings(first: int, count: int, ctx: Context) -> pa.Table:
    index = np.arange(first, first + count, dtype=np.int64)
    s = Stream(index, ctx.seed)
    vehicle = s.below("vehicle", 20000)
    v = Stream(vehicle, ctx.seed)
    when = seconds("2026-01-01T00:00:00") + s.below("when", TODAY_END - seconds("2026-01-01T00:00:00"))
    moving = ~s.chance("parked", 0.25)
    speed = np.where(moving, np.clip(55 + 25 * s.normal("speed"), 1, 140), 0.0)
    temp = np.where(moving, 88 + 6 * s.normal("temp"), 25 + 30 * s.u("cold") + 6 * s.normal("cold.noise"))
    fuel = 3 + 97 * s.u("fuel")
    odometer = 5000 + (v.u("odometer") * 245000).astype(np.int64) + ((when - seconds("2026-01-01T00:00:00")) // 86400) * 40
    lat = 50.0 + 5.5 * v.u("home.lat") + 0.05 * s.normal("lat")
    lon = -5.5 + 7.0 * v.u("home.lon") + 0.05 * s.normal("lon")
    has_fault = s.chance("fault", 0.004)
    fault = pa.array(np.array(FAULTS, dtype=object)[s.below("fault.code", len(FAULTS))], pa.string(), mask=~has_fault)
    return pa.table({
        "vehicle_id": number_string(vehicle + 1, 5, "VH"),
        "read_at": timestamps(when),
        "speed_kph": pa.array(np.round(speed, 1)),
        "engine_temp_c": pa.array(np.round(temp, 1)),
        "fuel_pct": pa.array(np.round(fuel, 1)),
        "odometer_km": pa.array(odometer),
        "latitude": pa.array(np.round(lat, 5)),
        "longitude": pa.array(np.round(lon, 5)),
        "fault_code": fault,
    }, schema=SPECS["vehicle_readings"].schema())


# ---- the specs ---------------------------------------------------------------------------------------------------------------------

DATE, STAMP, STAMP_MS = "yyyy-MM-dd", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss.SSS"
LOCK, READ, REPLACED = "lock", "readable", "replaced by the person code"
HEALTH_REASON = "Needed for the readmission research. Group sizes are checked before the table is shared."
CUSTOMER_REASON = "Needed for the spending analysis. Group sizes are checked before the table is shared."
APPROVER = "the data protection officer"
MRN_RULE = {"kind": "medical record number", "foundBy": "column", "column": "patient_number"}
CUSTOMER_RULE = {"kind": "customer number", "foundBy": "column", "column": "customer_number"}

SPECS: dict[str, TableSpec] = {}


def _add(spec: TableSpec) -> None:
    SPECS[spec.name] = spec


_add(TableSpec(
    "patients", "health", "people", (
        Col("registration_id", pa.string(), key=1, nullable=False),
        Col("patient_number", pa.string(), "direct", REPLACED, person_identifier=True),
        Col("full_name", pa.string(), "direct", LOCK),
        Col("date_of_birth", pa.date32(), "direct", LOCK, fmt=DATE),
        Col("sex", pa.string(), "quasi", READ),
        Col("address_line", pa.string(), "direct", LOCK),
        Col("town", pa.string(), "quasi", READ),
        Col("region", pa.string(), "quasi", READ),
        Col("postcode", pa.string(), "direct", LOCK),
        Col("phone", pa.string(), "direct", LOCK),
        Col("email", pa.string(), "direct", LOCK),
        Col("blood_group", pa.string(), "quasi", READ),
        Col("registered_on", pa.date32(), "none", READ, fmt=DATE)),
    _patients, "People registered with a hospital group: names, birth dates and contact details are locked.",
    rows_per_person=1, subject=MRN_RULE))

_add(TableSpec(
    "admissions", "health", "people", (
        Col("admission_id", pa.string(), key=1, nullable=False),
        Col("patient_number", pa.string(), "direct", REPLACED, person_identifier=True),
        Col("ward_code", pa.string(), "none", READ),
        Col("admitted_at", pa.timestamp("s"), "quasi", READ, fmt=STAMP, zone=False),
        Col("discharged_at", pa.timestamp("s"), "quasi", READ, fmt=STAMP, zone=False),
        Col("primary_diagnosis", pa.string(), "quasi", READ, reason=HEALTH_REASON, approved_by=APPROVER),
        Col("age_at_admission", pa.int64(), "quasi", READ),
        Col("length_of_stay_days", pa.int64(), "none", READ),
        Col("total_cost", pa.decimal128(12, 2), "none", READ, note="decimal(12,2)"),
        Col("outcome", pa.string(), "quasi", READ)),
    _admissions, "Hospital admissions over six and a half years, each tied to a person by record number.", rows_per_person=4, subject=MRN_RULE))

_add(TableSpec(
    "diagnosis_codes", "health", "plain", (
        Col("code", pa.string(), key=1, nullable=False),
        Col("description", pa.string()),
        Col("chapter", pa.string()),
        Col("typical_stay_days", pa.int64()),
        Col("relative_cost", pa.float64())),
    _diagnosis_codes, "A lookup of common diagnosis codes with a typical stay and a relative cost.", fixed_rows=len(pools.diagnoses()),
    not_erasable_reason="A published classification of diagnoses, not about any person."))

_add(TableSpec(
    "ward_occupancy", "health", "plain", (
        Col("ward_code", pa.string(), key=1, nullable=False),
        Col("hour_start", pa.timestamp("s"), key=2, nullable=False, fmt=STAMP, zone=False),
        Col("beds_open", pa.int64()),
        Col("beds_occupied", pa.int64()),
        Col("admissions_in_hour", pa.int64()),
        Col("discharges_in_hour", pa.int64())),
    _ward_occupancy, "Beds open and occupied for each of 800 wards, every hour.", large=True,
    not_erasable_reason="Counts about wards, not about people."))

_add(TableSpec(
    "customers", "finance", "people", (
        Col("customer_record", pa.string(), key=1, nullable=False),
        Col("customer_number", pa.string(), "direct", REPLACED, person_identifier=True),
        Col("full_name", pa.string(), "direct", LOCK),
        Col("date_of_birth", pa.date32(), "direct", LOCK, fmt=DATE),
        Col("national_id", pa.string(), "direct", LOCK),
        Col("address_line", pa.string(), "direct", LOCK),
        Col("town", pa.string(), "quasi", READ),
        Col("postcode", pa.string(), "direct", LOCK),
        Col("phone", pa.string(), "direct", LOCK),
        Col("email", pa.string(), "direct", LOCK),
        Col("segment", pa.string(), "none", READ),
        Col("risk_band", pa.string(), "quasi", READ),
        Col("joined_on", pa.date32(), "none", READ, fmt=DATE)),
    _customers, "Bank customers: names, birth dates, national numbers and contact details are locked.", rows_per_person=1, subject=CUSTOMER_RULE))

_add(TableSpec(
    "transactions", "finance", "people", (
        Col("transaction_id", pa.string(), key=1, nullable=False),
        Col("customer_number", pa.string(), "direct", REPLACED, person_identifier=True),
        Col("posted_at", pa.timestamp("s"), "quasi", READ, fmt=STAMP, zone=False),
        Col("amount", pa.decimal128(14, 2), "quasi", READ, reason=CUSTOMER_REASON, approved_by=APPROVER, note="decimal(14,2)"),
        Col("currency", pa.string(), "none", READ),
        Col("merchant_id", pa.string(), "none", READ),
        Col("merchant_category", pa.string(), "none", READ),
        Col("channel", pa.string(), "none", READ),
        Col("status", pa.string(), "none", READ)),
    _transactions, "Card transactions, each tied to a customer by number.", rows_per_person=20, subject=CUSTOMER_RULE))

_add(TableSpec(
    "merchant_categories", "finance", "plain", (
        Col("mcc", pa.string(), key=1, nullable=False),
        Col("description", pa.string()),
        Col("category_group", pa.string()),
        Col("typical_amount", pa.float64())),
    _merchant_categories, "A lookup of merchant category codes with a typical amount.", fixed_rows=len(pools.merchant_categories()),
    not_erasable_reason="A published list of merchant categories, not about any person."))

_add(TableSpec(
    "fx_ticks", "finance", "plain", (
        Col("pair", pa.string(), key=1, nullable=False),
        Col("ticked_at", pa.timestamp("ms"), key=2, nullable=False, fmt=STAMP_MS, zone=False),
        Col("bid", pa.decimal128(12, 6), note="decimal(12,6)"),
        Col("ask", pa.decimal128(12, 6), note="decimal(12,6)"),
        Col("venue", pa.string())),
    _fx_ticks, "Foreign exchange prices for twelve currency pairs, four a second.", large=True,
    not_erasable_reason="Market prices, not about any person."))

_add(TableSpec(
    "vehicle_readings", "vehicles", "plain", (
        Col("vehicle_id", pa.string()),
        Col("read_at", pa.timestamp("s"), fmt=STAMP, zone=False),
        Col("speed_kph", pa.float64()),
        Col("engine_temp_c", pa.float64()),
        Col("fuel_pct", pa.float64()),
        Col("odometer_km", pa.int64()),
        Col("latitude", pa.float64()),
        Col("longitude", pa.float64()),
        Col("fault_code", pa.string())),
    _vehicle_readings, "Readings from 20,000 vehicles: speed, temperature, fuel, position and fault codes. No key.", large=True,
    not_erasable_reason="Readings from vehicles, not about any person."))
