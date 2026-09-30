/**
 * Who you can act as, and how to describe them.
 *
 * The list of people is **not** here. It comes from `GET /directory`, because
 * the platform has a `directory` table and that is the answer to who exists. An
 * earlier version of this file carried the list and a comment claiming there was
 * no user table. There is one, and a console that invents people can offer a
 * custodian whose every approval the database will refuse by foreign key.
 *
 * What stays here is display copy: a sentence about what somebody does and what
 * the console is useful for when acting as them. That is written for a reader
 * and does not belong in an operational table.
 *
 * A person in the directory with no copy here is still shown, with their role.
 * The alternative is hiding registered people from a screen whose whole purpose
 * is to say who exists.
 */

export interface DirectoryEntry {
  id: string;
  label: string;
  kind: "human" | "workload";
  roles: string[];
  /** The organisation this person belongs to. Every list is scoped to it. */
  tenant_id: string;
  /** `production`, `canary` or `retired`. Decides whether a badge is shown. */
  tenant_purpose: string;
  department_name: string | null;
  department_id: string | null;
}

export interface Principal extends DirectoryEntry {
  /** What this person does, in the words they would use. */
  description: string;
  /** What the console is for when acting as them. */
  landing: string;
}

interface Copy {
  description: string;
  landing: string;
}

/**
 * Keyed by role rather than by person, so a second custodian needs no new copy
 * and a directory that grows does not need this file to grow with it.
 */
const BY_ROLE: Record<string, Copy> = {
  // data_custodian is absent on purpose. Its copy depends on whether the person
  // holds a department, so it is built by custodianCopy below rather than
  // sitting here and being contradicted afterwards.
  dpo: {
    description:
      "Independent oversight. Sees every decision including every refusal, and makes none.",
    landing: "The audit log, refusals first, and records that were erased.",
  },
  notebook_explore: {
    description:
      "Wants de-identified data for a project. Asks for access, never grants it.",
    landing: "What you can already read, and how to ask for the rest.",
  },
  pipeline_operator: {
    description:
      "Runs the de-identification pipeline and works out why a run failed.",
    landing: "How much has been sealed, released, and is still held back.",
  },
  platform_admin: {
    description:
      "Keeps the services running. Sees everything about the system and holds no standing access to what is in it.",
    landing: "Every part of the system, and what the platform holds overall.",
  },
};

const FALLBACK: Copy = {
  description: "Registered in the directory.",
  landing: "What this role can reach is decided by the policy engine.",
};

/**
 * Copy that depends on the appointment rather than only the role.
 *
 * A custodian's authority comes from the department they hold, not from the
 * role in the abstract. Describing them by role alone produced a card that said
 * "accountable for a department's data" and then, two lines later, "holds no
 * department, so can approve nothing", which is a contradiction the reader has
 * to resolve themselves.
 *
 * Named as a special case rather than generalised, because it is one: no other
 * role's meaning changes with a row in another table.
 */
function custodianCopy(entry: DirectoryEntry): Copy | null {
  if (!entry.roles.includes("data_custodian")) return null;

  if (entry.department_name) {
    return {
      description: `Accountable for ${entry.department_name}'s data. Decides who may read it, and for what.`,
      landing: `Requests waiting on your decision, and what ${entry.department_name} owns.`,
    };
  }

  return {
    description:
      "Holds the custodian role but is not appointed to any department, so there is nothing for them to be accountable for and nobody's request they can approve.",
    landing:
      "The same published data as anyone else. Approving needs an appointment.",
  };
}

/**
 * Can this person do anything the console can demonstrate?
 *
 * A custodian with no department approves nothing, and reads exactly what every
 * other human role reads. Offering them as a choice asks somebody to pick a
 * persona that shows less than the one below it.
 *
 * They are not hidden. They appear in the directory view, which is where the
 * question "who is registered" belongs. The chooser answers a different
 * question: who you can usefully be.
 *
 * The distinction matters because the state is legitimate. Somebody who moves
 * department or leaves must stay in the directory, or every approval they made
 * becomes unattributable, which is why the foreign key refuses to delete them.
 */
export function isAppointed(entry: DirectoryEntry): boolean {
  if (entry.roles.includes("data_custodian")) {
    return Boolean(entry.department_name);
  }
  return true;
}

export function describe(entry: DirectoryEntry): Principal {
  const copy =
    custodianCopy(entry) ??
    entry.roles.map((r) => BY_ROLE[r]).find(Boolean) ??
    FALLBACK;
  return { ...entry, ...copy };
}

