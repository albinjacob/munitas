"""U167: a notebook signs in on behalf of a person, and the pass it receives can do only what a notebook may.

The notebook shows a code; the person types it in the console, is signed in by the console's own session, and approves; the notebook receives a short-lived pass
from Hydra. Everything is asked of the live platform and the live Hydra, one item at a time, playing both parts: the NOTEBOOK (starts, polls, renews) and the
PERSON (a browser that follows Hydra's redirects, and answers each console step the way the console does, with a real session).

  * what the notebook is shown and what is kept: a code, an address, a device code; only hashes are stored;
  * the person's steps: a code the platform did not start is refused, wrong codes are limited, the sign-in is the console session and only for the person who
    typed the code, and the approval says what the notebook will and will not be able to do;
  * the pass: opaque, short-lived, naming the person and the sign-in; accepted for the routes the client library calls, and refused, with a reason, for every
    other route, including one the person may use in the console (deciding a request);
  * renewal: a new pass each time, and an old renewal token used again ends the sign-in;
  * deny: the notebook is told at once, which Hydra alone does not do;
  * revoke: the pass and the renewal token stop at the next request, and the list says who and when;
  * who sees the list: the person their own, the data protection officer and the privacy auditor everybody's, nobody else;
  * a pass used from another address is counted, and a sign-in that has run out is refused;
  * the client library: `Munitas.sign_in` shows the code and the address, waits, and returns a notebook that works, renews its own pass, ends its own sign-in,
    and is told plainly when the person refused.

    docker compose exec -T munitas-api python /verify/v160_notebook_sign_in.py
"""

from __future__ import annotations

import re
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")
sys.path.insert(0, "/client")
sys.path.insert(0, "/step-sdk")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER  # noqa: E402
from v140_custom_steps_and_files import reasons_of  # noqa: E402
from munitas_client import Munitas, MunitasError  # noqa: E402

BROWSER_HYDRA, INTERNAL_HYDRA, HYDRA_ADMIN = "http://localhost:4444", "http://hydra:4444", "http://hydra:4445"
DPO, AUDITOR = "canary-dpo", "canary-auditor"
LABEL = "U167 notebook"


def inside(url: str) -> str:
    """Hydra answers the browser with its public address; from here the same service is `hydra`."""
    return url.replace(BROWSER_HYDRA, INTERNAL_HYDRA)


def start(label: str = LABEL, **kw) -> httpx.Response:
    return httpx.post(f"{API}/notebook/v1/sign-in", json={"label": label}, timeout=30.0, **kw)


def poll(device_code: str) -> httpx.Response:
    return httpx.post(f"{API}/notebook/v1/sign-in/token", json={"device_code": device_code}, timeout=30.0)


def renew(refresh_token: str) -> httpx.Response:
    return httpx.post(f"{API}/notebook/v1/sign-in/refresh", json={"refresh_token": refresh_token}, timeout=30.0)


def approve_in_browser(person: str, started: dict, approve: bool = True) -> tuple[list[str], httpx.Response | None]:
    """The person's browser: open the address, type the code, be signed in by the console session, approve or refuse. Returns the steps and any refusal."""
    browser = httpx.Client(follow_redirects=False, timeout=30.0)
    headers = bearer_for(person)
    url, steps = inside(started["verification_uri_complete"]), []
    for _ in range(30):
        r = browser.get(url)
        if r.status_code not in (301, 302, 303, 307):
            steps.append(f"stopped at {urlparse(url).path} with {r.status_code}")
            return steps, r
        where = urljoin(url, r.headers["Location"])
        path, query = urlparse(where).path, {k: v[0] for k, v in parse_qs(urlparse(where).query).items()}
        steps.append(path)
        if path == "/notebook-sign-in/code":
            step = api("POST", "/notebook-sign-in/code", json={"device_challenge": query["device_challenge"], "user_code": started["user_code"]}, headers=headers)
        elif path == "/notebook-sign-in/login":
            step = api("POST", "/notebook-sign-in/login", json={"login_challenge": query["login_challenge"]}, headers=headers)
        elif path == "/notebook-sign-in/consent":
            shown = api("GET", "/notebook-sign-in/consent", params={"consent_challenge": query["consent_challenge"]}, headers=headers)
            if shown.status_code != 200:
                return steps, shown
            step = api("POST", "/notebook-sign-in/consent", json={"consent_challenge": query["consent_challenge"], "approve": approve}, headers=headers)
        elif path == "/notebook-sign-in/done":
            return steps, None
        else:
            url = inside(where)
            continue
        if step.status_code != 200:
            return steps, step
        url = inside(step.json()["redirect_to"])
    return steps, None


def row_of(device_code: str) -> dict:
    from app.notebook_sign_in import digest
    with db() as conn:
        return conn.execute("select * from notebook_sign_in where device_code_hash = %s", (digest(device_code),)).fetchone()


def sign_in(person: str, label: str = LABEL) -> tuple[dict, dict]:
    """The whole thing, for the checks that need a notebook that is already signed in. Returns what the notebook was shown and the tokens it received."""
    started = start(label).json()
    approve_in_browser(person, started)
    for _ in range(10):
        got = poll(started["device_code"])
        if got.status_code == 200:
            return started, got.json()
        time.sleep(5.2)
    raise AssertionError(f"no token for {person}: {got.status_code} {got.text[:200]}")


def row_by_id(sign_in_id: str) -> dict:
    with db() as conn:
        return conn.execute("select * from notebook_sign_in where id = %s", (sign_in_id,)).fetchone()


def _expect_error(call) -> Exception | None:
    try:
        call()
    except MunitasError as exc:
        return exc
    return None


def with_pass(token: str, method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=30.0, **kw)


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    with db() as conn:
        conn.execute("delete from notebook_sign_in where label like 'U167 %'")
        conn.execute("delete from notebook_code_attempt where principal in (%s, %s)", (ENGINEER, RESEARCHER))
        conn.commit()

    heading("The notebook asks to sign in")
    r = start()
    shown = r.json()
    check("it is told a code, an address to open, a device code and how often to ask", r.status_code == 201 and len(shown["user_code"]) >= 6 and "oauth2/device/verify" in shown["verification_uri_complete"]
          and len(shown["device_code"]) > 40 and shown["interval"] >= 5 and 0 < shown["expires_in"] <= 600, str(shown)[:260])
    stored = row_of(shown["device_code"])
    check("what is kept is a pending row with hashes, never the code or the device code", stored["status"] == "pending" and stored["principal"] is None
          and shown["user_code"] not in str(dict(stored)) and shown["device_code"] not in str(dict(stored)) and stored["label"] == LABEL, str(dict(stored))[:200])
    first = poll(shown["device_code"])
    check("asking before anybody approved gets 'pending'", first.status_code == 400 and first.json()["error"] == "authorization_pending", f"{first.status_code} {first.text[:160]}")
    quick = poll(shown["device_code"])
    check("asking again at once is told to slow down", quick.status_code == 400 and quick.json()["error"] == "slow_down", f"{quick.status_code} {quick.text[:160]}")
    check("a device code nobody started is refused", poll("not-a-device-code-" + uuid.uuid4().hex).json()["error"] == "invalid_grant")

    heading("The person's steps")
    direct = httpx.post(f"{INTERNAL_HYDRA}/oauth2/device/auth", data={"client_id": "munitas-notebook", "scope": "offline_access notebook"}, timeout=30.0).json()
    steps, refused = approve_in_browser(RESEARCHER, direct)
    check("a code that Hydra made but this platform did not start is refused", refused is not None and refused.status_code == 400 and "not a code a notebook is waiting on" in reasons_of(refused),
          f"{steps} {refused.status_code if refused is not None else None}")
    wrong = [api("POST", "/notebook-sign-in/code", json={"device_challenge": "x", "user_code": f"WRONG{i}AB"}, headers=bearer_for(ENGINEER)).status_code for i in range(7)]
    check("wrong codes are refused, and after five the person is told to wait", wrong[:5] == [400] * 5 and wrong[5] == 429 and wrong[6] == 429, str(wrong))
    steps, refused = approve_in_browser(RESEARCHER, shown)
    check("the person opens the address, types the code, is signed in by the console, and approves", refused is None and steps[-1] == "/notebook-sign-in/done", str(steps))
    approved = row_of(shown["device_code"])
    check("the row now says who, when and until when", approved["status"] == "approved" and approved["principal"] == RESEARCHER and approved["tenant_id"] == CANARY
          and 7.9 * 3600 < (approved["expires_at"] - approved["approved_at"]).total_seconds() <= 8 * 3600, str(dict(approved))[:240])
    time.sleep(5.3)
    got = poll(shown["device_code"])
    tokens = got.json()
    check("the notebook receives a pass and a renewal token, short-lived", got.status_code == 200 and tokens["access_token"].startswith("ory_at_") and tokens["refresh_token"].startswith("ory_rt_")
          and 800 <= tokens["expires_in"] <= 900 and set(tokens["scope"].split()) == {"notebook", "offline_access"}, str({k: str(v)[:20] for k, v in tokens.items()}))
    seen = httpx.post(f"{HYDRA_ADMIN}/admin/oauth2/introspect", data={"token": tokens["access_token"]}, timeout=30.0).json()
    check("Hydra says it is live, for the person's directory id, for the notebook client, naming the sign-in", seen["active"] is True and seen["sub"] == RESEARCHER
          and seen["client_id"] == "munitas-notebook" and seen["ext"]["sign_in"] == str(approved["id"]), str(seen)[:260])
    again = poll(shown["device_code"])
    check("the same device code cannot be used again", again.status_code == 400)

    heading("What the pass may reach")
    who = with_pass(tokens["access_token"], "GET", "/auth/whoami")
    check("the library's calls work: who am I", who.status_code == 200 and who.json()["id"] == RESEARCHER and who.json()["session_id"] == str(approved["id"]), f"{who.status_code} {who.text[:200]}")
    check("and finding datasets", with_pass(tokens["access_token"], "GET", "/datasets", params={"q": "x"}).status_code == 200)
    after_use = row_of(shown["device_code"])
    check("using it is written on the row: when, from where, with what", after_use["last_used_at"] is not None and bool(after_use["first_ip"]) and after_use["address_changes"] == 0)
    refusals = {
        "deciding a request": ("POST", f"/leases/requests/{uuid.uuid4()}/approve"),
        "reading the list of notebook sign-ins": ("GET", "/notebook-sign-ins"),
        "ending a lease": ("POST", f"/leases/{uuid.uuid4()}/revoke"),
        "asking for a role": ("POST", "/people/role-requests"),
        "the console's own request list": ("GET", "/lease-requests"),
    }
    for label, (method, path) in refusals.items():
        got_it = with_pass(tokens["access_token"], method, path, **({"json": {}} if method == "POST" else {}))
        check(f"{label} is refused with a reason, before the route is asked", got_it.status_code == 403 and "reaches only what the client library does" in reasons_of(got_it), f"{got_it.status_code} {got_it.text[:140]}")
    check("a notebook pass is refused where a files token is wanted", httpx.get(f"{API}/notebook/v1/files", headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=30.0).status_code == 401)
    custodian_started, custodian_tokens = sign_in(CUSTODIAN)
    decide = with_pass(custodian_tokens["access_token"], "POST", f"/leases/requests/{uuid.uuid4()}/approve")
    check("a data custodian's notebook pass cannot decide a request either, though the custodian can in the console", decide.status_code == 403 and "reaches only" in reasons_of(decide))

    heading("A pass from another address is counted")
    own = socket.gethostbyname(socket.gethostname())
    other = httpx.get(f"http://{own}:8000/auth/whoami", headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=30.0)
    flagged = row_of(shown["device_code"])
    check("it still works, and the row counts the change of address", other.status_code == 200 and flagged["address_changes"] == 1 and flagged["last_ip"] != flagged["first_ip"], f"{other.status_code} {flagged['first_ip']} {flagged['last_ip']} {flagged['address_changes']}")

    heading("Renewal")
    new = renew(tokens["refresh_token"])
    check("renewing gives a new pass and a new renewal token", new.status_code == 200 and new.json()["access_token"] != tokens["access_token"] and new.json()["refresh_token"] != tokens["refresh_token"], f"{new.status_code} {new.text[:160]}")
    check("the new pass works", with_pass(new.json()["access_token"], "GET", "/auth/whoami").status_code == 200)
    reuse = renew(tokens["refresh_token"])
    check("the old renewal token used again is refused", reuse.status_code == 400, f"{reuse.status_code} {reuse.text[:160]}")
    check("and that ended the newer pass too", with_pass(new.json()["access_token"], "GET", "/auth/whoami").status_code == 401)

    heading("Refusing")
    denied_started = start("U167 denied").json()
    steps, nothing = approve_in_browser(RESEARCHER, denied_started, approve=False)
    time.sleep(5.3)
    told = poll(denied_started["device_code"])
    check("when the person presses Deny the notebook is told at once", told.status_code == 400 and told.json()["error"] == "access_denied", f"{told.status_code} {told.text[:160]} {steps}")
    check("and the row says who refused", row_of(denied_started["device_code"])["status"] == "denied" and row_of(denied_started["device_code"])["ended_by"] == RESEARCHER)

    heading("Ending it")
    live_started, live = sign_in(RESEARCHER, "U167 to end")
    live_id = str(row_of(live_started["device_code"])["id"])
    check("the person sees their sign-ins, newest first, with when, where from and until when", any(s["id"] == live_id and s["in_force"] for s in api("GET", "/notebook-sign-ins", headers=bearer_for(RESEARCHER)).json()["sign_ins"]))
    check("somebody else cannot end it", api("POST", f"/notebook-sign-ins/{live_id}/revoke", headers=bearer_for(ENGINEER)).status_code == 404)
    ended = api("POST", f"/notebook-sign-ins/{live_id}/revoke", headers=bearer_for(RESEARCHER))
    check("the person ends it", ended.status_code == 200 and ended.json()["status"] == "revoked" and ended.json()["in_force"] is False, f"{ended.status_code} {ended.text[:160]}")
    check("the pass stops at the next request", with_pass(live["access_token"], "GET", "/auth/whoami").status_code == 401)
    check("and the renewal token stops too", renew(live["refresh_token"]).status_code == 400)
    check("the row says who ended it and when", row_of(live_started["device_code"])["ended_by"] == RESEARCHER and row_of(live_started["device_code"])["ended_at"] is not None)
    check("ending it again is refused", api("POST", f"/notebook-sign-ins/{live_id}/revoke", headers=bearer_for(RESEARCHER)).status_code == 409)

    heading("The client library signs a notebook in")
    shown_lines: list[str] = []
    outcome: dict = {}

    def run_sign_in(label: str, key: str, wait: float | None = None) -> None:
        try:
            outcome[key] = Munitas.sign_in(API, label, show=shown_lines.append, wait=wait)
        except MunitasError as exc:
            outcome[key] = exc

    def shown_code() -> dict:
        for _ in range(60):
            if shown_lines:
                text = shown_lines[-1]
                return {"verification_uri_complete": re.search(r"open (\S+) and", text).group(1), "user_code": re.search(r"code (\w+)\.", text).group(1), "text": text}
            time.sleep(0.5)
        raise AssertionError("the library showed nothing")

    worker = threading.Thread(target=run_sign_in, args=("U167 library", "ok"))
    worker.start()
    told = shown_code()
    check("it shows the address, the code and the name the person will see", "oauth2/device/verify" in told["verification_uri_complete"] and "U167 library" in told["text"], told["text"][:200])
    steps, refused = approve_in_browser(RESEARCHER, told)
    worker.join(60)
    notebook = outcome.get("ok")
    check("after the person approves, sign_in returns a notebook that works", isinstance(notebook, Munitas) and notebook.me["id"] == RESEARCHER and refused is None, str(outcome)[:200])
    first_pass = notebook._pass
    check("it holds a pass and a renewal token, in memory", first_pass.startswith("ory_at_") and notebook._refresh.startswith("ory_rt_"))
    notebook._pass_until = 0.0
    notebook.me  # noqa: B018 - already cached; ask for something that goes to the platform
    found = notebook._call("GET", "/datasets", params={"q": "none-such"})
    check("when the pass is about to run out the next call renews it first, and still works", notebook._pass != first_pass and "datasets" in found, str(found)[:120])
    check("a notebook cannot do what the console does: deciding a request", "reaches only" in str(_expect_error(lambda: notebook._call("POST", f"/leases/requests/{uuid.uuid4()}/approve"))))
    own_id = notebook.me["session_id"]
    notebook.sign_out()
    check("sign_out ends the sign-in at once", row_by_id(own_id)["status"] == "revoked" and row_by_id(own_id)["ended_by"] == RESEARCHER)
    check("and the notebook says plainly that it is not signed in any more", "not signed in" in str(_expect_error(lambda: notebook._call("GET", "/auth/whoami"))))

    shown_lines.clear()
    outcome.clear()
    worker = threading.Thread(target=run_sign_in, args=("U167 library refused", "no"))
    worker.start()
    told = shown_code()
    approve_in_browser(RESEARCHER, told, approve=False)
    worker.join(60)
    check("when the person refuses, sign_in says so", isinstance(outcome.get("no"), MunitasError) and "refused" in str(outcome["no"]), str(outcome)[:200])
    check("an address that is not https is refused unless it is this machine", "not https" in str(_expect_error(lambda: Munitas("http://munitas.example.org", "x"))))
    check("the console's own token still works as before", Munitas(API, bearer_for(RESEARCHER)["Authorization"].split(" ", 1)[1]).me["id"] == RESEARCHER)

    heading("Ending one of two")
    laptop_started, laptop = sign_in(RESEARCHER, "U167 laptop")
    server_started, server = sign_in(RESEARCHER, "U167 server")
    server_id = str(row_of(server_started["device_code"])["id"])
    check("each sign-in is tied to Hydra's own id for its approval", row_of(laptop_started["device_code"])["consent_request_id"] and row_of(server_started["device_code"])["consent_request_id"]
          and row_of(laptop_started["device_code"])["consent_request_id"] != row_of(server_started["device_code"])["consent_request_id"])
    check("both work", with_pass(laptop["access_token"], "GET", "/auth/whoami").status_code == 200 and with_pass(server["access_token"], "GET", "/auth/whoami").status_code == 200)
    ended_one = api("POST", f"/notebook-sign-ins/{server_id}/revoke", headers=bearer_for(RESEARCHER))
    check("the person ends the server's", ended_one.status_code == 200 and ended_one.json()["status"] == "revoked", f"{ended_one.status_code} {ended_one.text[:140]}")
    check("the server's pass and renewal token stop", with_pass(server["access_token"], "GET", "/auth/whoami").status_code == 401 and renew(server["refresh_token"]).status_code == 400)
    check("the laptop's pass still works", with_pass(laptop["access_token"], "GET", "/auth/whoami").status_code == 200)
    laptop_renewed = renew(laptop["refresh_token"])
    check("and so does its renewal", laptop_renewed.status_code == 200 and with_pass(laptop_renewed.json()["access_token"], "GET", "/auth/whoami").status_code == 200, f"{laptop_renewed.status_code}")
    check("the laptop's row is still in force and the server's says who ended it", row_of(laptop_started["device_code"])["status"] == "approved"
          and row_of(server_started["device_code"])["status"] == "revoked" and row_of(server_started["device_code"])["ended_by"] == RESEARCHER)
    with db() as conn:
        conn.execute("update notebook_sign_in set consent_request_id = null where device_code_hash = %s", (row_of(laptop_started["device_code"])["device_code_hash"],))
        conn.commit()
    older = api("POST", f"/notebook-sign-ins/{row_of(laptop_started['device_code'])['id']}/revoke", headers=bearer_for(RESEARCHER))
    check("a sign-in recorded without Hydra's id can still be ended, with the person's others", older.status_code == 200 and with_pass(laptop_renewed.json()["access_token"], "GET", "/auth/whoami").status_code == 401)

    heading("Knowing which are in use, and ending all of them")
    first_started, first = sign_in(RESEARCHER, "U167 first")
    second_started, second = sign_in(RESEARCHER, "U167 second")
    with_pass(first["access_token"], "GET", "/auth/whoami")
    by_label = {x["label"]: x for x in api("GET", "/notebook-sign-ins", headers=bearer_for(RESEARCHER)).json()["sign_ins"]}
    check("a notebook used a moment ago is marked in use, and one not used yet is not",
          by_label["U167 first"]["in_use"] is True and by_label["U167 second"]["in_use"] is False and by_label["U167 first"]["in_use_minutes"] == 10, str({k: v["in_use"] for k, v in by_label.items() if k.startswith("U167 f")}))
    with db() as conn:
        conn.execute("update notebook_sign_in set last_used_at = now() - interval '20 minutes' where device_code_hash = %s", (row_of(first_started["device_code"])["device_code_hash"],))
        conn.commit()
    quiet = {x["label"]: x for x in api("GET", "/notebook-sign-ins", headers=bearer_for(RESEARCHER)).json()["sign_ins"]}
    check("one last used twenty minutes ago is not marked in use", quiet["U167 first"]["in_use"] is False)
    with_pass(first["access_token"], "GET", "/auth/whoami")
    refused_all = with_pass(first["access_token"], "POST", "/notebook-sign-ins/revoke-all")
    check("a notebook's own pass cannot end everything: only the console can", refused_all.status_code == 403 and "reaches only" in reasons_of(refused_all), f"{refused_all.status_code} {refused_all.text[:120]}")
    ended_all = api("POST", "/notebook-sign-ins/revoke-all", headers=bearer_for(RESEARCHER))
    names = {x["label"] for x in ended_all.json().get("sign_ins", [])}
    check("the person ends all of theirs at once, and is told which", ended_all.status_code == 200 and {"U167 first", "U167 second"} <= names and ended_all.json()["ended"] >= 2, f"{ended_all.status_code} {ended_all.text[:200]}")
    check("every pass and renewal token stops", all(with_pass(t["access_token"], "GET", "/auth/whoami").status_code == 401 for t in (first, second)) and all(renew(t["refresh_token"]).status_code == 400 for t in (first, second)))
    check("the rows say who ended them and when", all(row_of(x["device_code"])["status"] == "revoked" and row_of(x["device_code"])["ended_by"] == RESEARCHER for x in (first_started, second_started)))
    check("another person's sign-ins are not touched", with_pass(custodian_tokens["access_token"], "GET", "/auth/whoami").status_code == 200)
    check("ending all when none is in force ends none", api("POST", "/notebook-sign-ins/revoke-all", headers=bearer_for(RESEARCHER)).json()["ended"] == 0)

    heading("Running out")
    run_started, run = sign_in(RESEARCHER, "U167 to run out")
    with db() as conn:
        conn.execute("update notebook_sign_in set expires_at = now() - interval '1 minute' where device_code_hash = %s", (row_of(run_started["device_code"])["device_code_hash"],))
        conn.commit()
    check("a sign-in whose time has run out is refused at its next request, whatever Hydra says", with_pass(run["access_token"], "GET", "/auth/whoami").status_code == 401)

    heading("Who sees the list")
    mine = api("GET", "/notebook-sign-ins", headers=bearer_for(ENGINEER)).json()["sign_ins"]
    check("another person sees none of the researcher's", all(s["person"] != "Canary researcher" for s in mine) and not any(s["id"] == live_id for s in mine))
    check("an ordinary person may not ask for everybody's", api("GET", "/notebook-sign-ins", params={"everyone": "true"}, headers=bearer_for(RESEARCHER)).status_code == 403)
    for who_is in (DPO, AUDITOR):
        everyone = api("GET", "/notebook-sign-ins", params={"everyone": "true"}, headers=bearer_for(who_is))
        check(f"{who_is} reads everybody's, with the person named", everyone.status_code == 200 and any(s["id"] == live_id and s["person"] for s in everyone.json()["sign_ins"]), f"{everyone.status_code}")

    heading("Starting too many")
    mine_ip = socket.gethostbyname(socket.gethostname())
    with db() as conn:
        for i in range(30):
            conn.execute("insert into notebook_sign_in (id, user_code_hash, device_code_hash, label, created_ip) values (gen_random_uuid(), %s, %s, 'U167 filler', %s)",
                         (f"filler-{i}", f"filler-device-{uuid.uuid4().hex}", mine_ip))
        conn.commit()
    flood = httpx.post(f"http://{mine_ip}:8000/notebook/v1/sign-in", json={"label": "U167 over the limit"}, timeout=30.0)
    check("one address starting too many is told to wait", flood.status_code == 429, f"{flood.status_code} {flood.text[:140]}")

    with db() as conn:
        conn.execute("delete from notebook_sign_in where label like 'U167 %'")
        conn.execute("delete from notebook_code_attempt where principal in (%s, %s)", (ENGINEER, RESEARCHER))
        conn.commit()
    return summary("U167")


if __name__ == "__main__":
    sys.exit(main())
