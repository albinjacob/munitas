/**
 * Clears what automated test runs leave behind in a custodian's queue, so a capture shows the queue as the story needs it.
 *
 * The console tests ask for access on a workload's behalf and leave the request pending. Left alone, a custodian's home page counts those
 * as waiting, which buries the one request a walkthrough is about and makes its counts wrong. Only those requests are closed, matched by
 * the purpose the tests give them, and each is closed through the same route a custodian uses, with a reason that says so.
 */

import { bearerFor } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";

// What the tests write as the purpose of a request. A request is closed only when its purpose starts with one of these.
const TEST_PURPOSES = [
  "queue rendering check",
  "U56 real-session check",
  "playwright check",
];

export async function closeTestRequests(custodian: string, alsoPurposes: string[] = []): Promise<number> {
  const headers = { "content-type": "application/json", ...(await bearerFor(custodian)) };
  const listed = await fetch(`${API_BASE}/lease-requests?state=pending&custodian=${custodian}&limit=500`, { headers });
  if (!listed.ok) throw new Error(`could not list the queue of ${custodian}: ${listed.status}`);
  const { lease_requests = [] } = (await listed.json()) as {
    lease_requests?: { id: string; purpose: string }[];
  };
  let closed = 0;
  for (const r of lease_requests) {
    const purpose = String(r.purpose);
    if (![...TEST_PURPOSES, ...alsoPurposes].some((p) => purpose.startsWith(p))) continue;
    const res = await fetch(`${API_BASE}/leases/requests/${r.id}/reject`, {
      method: "POST",
      headers,
      body: JSON.stringify({ reason: "automated test request, closed before a capture" }),
    });
    if (!res.ok) throw new Error(`could not close request ${r.id}: ${res.status} ${await res.text()}`);
    closed += 1;
  }
  return closed;
}

/**
 * Revokes access that an earlier recording of the same walkthrough left open, so the count of granted access starts where the story says
 * it does. Only leases held by `principal` for the stated `purpose` are touched, each through the route a custodian uses.
 */
export async function revokeEarlierRecordings(custodian: string, principal: string, purpose: string): Promise<number> {
  const headers = { "content-type": "application/json", ...(await bearerFor(custodian)) };
  const listed = await fetch(`${API_BASE}/leases?principal=${principal}&active_only=true`, { headers });
  if (!listed.ok) throw new Error(`could not list the access held by ${principal}: ${listed.status}`);
  const { leases = [] } = (await listed.json()) as { leases?: { id: string; purpose?: string }[] };
  let revoked = 0;
  for (const l of leases) {
    if (String(l.purpose ?? "") !== purpose) continue;
    const res = await fetch(`${API_BASE}/leases/${l.id}/revoke`, { method: "POST", headers });
    if (!res.ok) throw new Error(`could not revoke ${l.id}: ${res.status} ${await res.text()}`);
    revoked += 1;
  }
  return revoked;
}
