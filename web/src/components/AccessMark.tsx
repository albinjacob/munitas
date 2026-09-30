/**
 * One version's access, in words for the person reading it.
 *
 * Every screen that says whether somebody can read a version says it through
 * this, so the wording is decided once. A list gets a short label with its
 * detail underneath; a page (`detailed`) gets the same thing as one sentence.
 */
import type { VersionAccess } from "../api/types";

function day(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

const TONE = {
  yes: "text-teal-800",
  wait: "text-amber-800",
  no: "text-slate-600",
} as const;

interface Wording {
  label: string;
  detail: string | null;
  sentence: string;
  tone: string;
}

function wording(access: VersionAccess): Wording {
  const decider = access.decider_label ?? "the person answerable for it";
  switch (access.mark) {
    case "removed":
      return {
        label: "Its files were removed",
        detail: null,
        sentence: "Its files were removed, so there is nothing left to read.",
        tone: TONE.no,
      };
    case "role":
      return {
        label: "You can read this",
        detail: null,
        sentence: "You can read this. Your role covers it.",
        tone: TONE.yes,
      };
    case "lease": {
      const until = access.until
        ? `You can read this until ${day(access.until)}`
        : "You can read this until somebody withdraws it";
      const purpose = access.purpose ? `for ${access.purpose}` : null;
      return {
        label: until,
        detail: purpose,
        sentence: purpose ? `${until} ${purpose}.` : `${until}.`,
        tone: TONE.yes,
      };
    }
    case "pending":
      return {
        label: "Asked, waiting",
        detail: `${decider} decides`,
        sentence: `You have asked, and ${decider} decides.`,
        tone: TONE.wait,
      };
    case "ended": {
      const when = access.ended_at
        ? `${access.ended === "revoked" ? "Withdrawn" : "Ran out"} on ${day(access.ended_at)}`
        : null;
      return {
        label: "Your access ended",
        detail: when,
        sentence: when
          ? `Your access ended. ${when}. Ask again if you still need it.`
          : "Your access ended. Ask again if you still need it.",
        tone: TONE.no,
      };
    }
    case "ask":
      return {
        label: "Needs a request",
        detail: null,
        sentence: `This is more sensitive than your role reads, so ${decider} decides.`,
        tone: TONE.no,
      };
    case "no_approver":
      return {
        label: "Nobody can grant this",
        detail: "No department owns it",
        sentence:
          "Nobody can grant this, because no department owns it. Ask the platform administrator.",
        tone: TONE.no,
      };
  }
}

export function AccessMark({
  access,
  failed,
  detailed = false,
}: {
  access: VersionAccess | undefined;
  failed: boolean;
  detailed?: boolean;
}) {
  if (failed) {
    return (
      <span data-testid="access-mark" data-mark="unknown" className={TONE.no}>
        {detailed
          ? "Could not check whether you can read this. Try again shortly."
          : "Couldn't check"}
      </span>
    );
  }
  if (!access) return <span className="text-slate-400">Checking</span>;

  const w = wording(access);
  return (
    <span data-testid="access-mark" data-mark={access.mark} className={w.tone}>
      {detailed ? w.sentence : w.label}
      {!detailed && w.detail ? (
        <span className="block text-xs text-slate-500">{w.detail}</span>
      ) : null}
    </span>
  );
}
