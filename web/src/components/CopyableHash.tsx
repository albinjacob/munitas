/**
 * A long hash or path, truncated with a click-to-copy affordance.
 *
 * A raw 64-character sha256 string printed inline is exactly correct for
 * audit transparency (the console should never hide what a value actually
 * is) and exactly wrong for scanning a page: it dominates the layout and
 * nobody reads it character by character anyway. Truncating the display
 * while keeping the full value one click away (and in `title`, for a plain
 * hover) keeps both: the value is never hidden, only not shoved in front of
 * everything else by default.
 */

import { useState } from "react";

const HEAD = 14;
const TAIL = 8;

export function CopyableHash({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  if (value.length <= HEAD + TAIL + 1) {
    // Short enough already; truncating it would save nothing and just add
    // an ellipsis to a value that was already readable in full.
    return <code className="break-all font-mono text-xs">{value}</code>;
  }

  const short = `${value.slice(0, HEAD)}…${value.slice(-TAIL)}`;

  return (
    <button
      type="button"
      title={value}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard access can be refused (no permission, insecure
          // context); the full value is still in `title` either way, so
          // this is a lost convenience, not a lost capability.
        }
      }}
      className="group inline-flex items-center gap-1 rounded font-mono text-xs text-slate-700 hover:text-indigo-700"
    >
      <span>{short}</span>
      {copied ? (
        <span className="text-emerald-700">Copied</span>
      ) : (
        <span className="text-slate-400 opacity-0 group-hover:opacity-100">
          copy
        </span>
      )}
    </button>
  );
}
