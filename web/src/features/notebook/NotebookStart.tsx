/**
 * How to use Munitas from a notebook: install the library from this platform, sign in as yourself, find data and ask for it.
 *
 * The install line is built from what the platform itself offers (`GET /client`) and the address this console talks to, so it is always the version this platform
 * was built with and the address a notebook on the same network reaches. Nothing here is typed by hand about the version.
 */

import { useState } from "react";
import { useClientLibrary } from "../../api/clientLibrary";
import { API_BASE } from "../../config/ports";

function Code({ testid, text }: { testid: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative mt-1 max-w-3xl">
      <pre data-testid={testid} className="overflow-x-auto rounded border border-slate-300 bg-slate-50 p-3 pr-20 text-xs text-slate-900">
        {text}
      </pre>
      <button
        type="button"
        data-testid={`${testid}-copy`}
        className="btn btn-secondary btn-sm absolute right-2 top-2"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            // Clipboard access can be refused; the text is on the page to select either way.
          }
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="mt-4" data-testid={`notebook-start-step-${n}`}>
      <h3 className="text-sm font-semibold text-slate-900">
        {n}. {title}
      </h3>
      <div className="mt-1 text-sm text-slate-800">{children}</div>
    </li>
  );
}

export function NotebookStart() {
  const lib = useClientLibrary();
  const [open, setOpen] = useState(true);
  const info = lib.data;
  const address = API_BASE.replace(/\/$/, "");
  const url = info?.available && info.path ? `${address}${info.path}` : "";
  return (
    <section data-testid="notebook-start" className="mb-6 rounded border border-slate-300 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-900">Use Munitas from a notebook</h2>
        <button type="button" data-testid="notebook-start-toggle" className="btn btn-ghost btn-sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : "Show"}
        </button>
      </div>
      {open && (
        <>
          {lib.isLoading && <p className="mt-2 text-sm text-slate-600">Looking for the library.</p>}
          {lib.error && (
            <p role="alert" className="mt-2 text-sm text-red-800">
              The library's details could not be read from the platform.
            </p>
          )}
          {info && !info.available && (
            <p data-testid="notebook-start-unavailable" className="mt-2 text-sm text-slate-800">
              This platform does not offer the library for download. Ask the person who runs it.
            </p>
          )}
          {info?.available && (
            <ol className="list-none">
              <Step n={1} title="Install the library">
                <p>Run this in the notebook, or in its terminal. It is version {info.version}, the one this platform was built with, and needs Python {info.requires_python?.replace(">=", "")} or later.</p>
                <Code testid="notebook-start-install" text={`pip install ${url}`} />
                <p className="mt-2">To read tables in DuckDB as well, install it with the tables extra instead:</p>
                <Code testid="notebook-start-install-tables" text={`pip install "munitas[tables] @ ${url}"`} />
                <p className="mt-2 text-xs text-slate-600">
                  SHA-256 of the file, to check what you downloaded: <span data-testid="notebook-start-sha" className="font-mono">{info.sha256}</span>
                </p>
              </Step>
              <Step n={2} title="Sign in as yourself">
                <Code testid="notebook-start-signin" text={`from munitas import Munitas\nme = Munitas.sign_in("${address}")`} />
                <p className="mt-2">It shows a code and an address. Open the address in this browser, type the code, and approve. The notebook then acts as you for up to eight hours and appears in the list below.</p>
              </Step>
              <Step n={3} title="Find data and ask for it">
                <Code
                  testid="notebook-start-ask"
                  text={`me.dataset("name-of-a-dataset")\nme.request_access("name-of-a-dataset", purpose="what it is for", justification="why you need it")`}
                />
                <p className="mt-2">An approver in the department that owns the data decides. Once it is approved, open the data in DuckDB:</p>
                <Code testid="notebook-start-read" text={`con = me.attach_duckdb(me.catalog_token("what it is for"))\ncon.sql("show all tables")`} />
              </Step>
            </ol>
          )}
        </>
      )}
    </section>
  );
}
