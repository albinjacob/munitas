r"""Render docs/public/reference/openapi.json into docs/public/reference/api-reference.html.

A hand-authored renderer rather than a vendored Swagger UI/Redoc bundle,
because this repo doesn't pull anything from a CDN anywhere else in its
docs, and a self-contained page needs no exception for its API reference.
Regenerate after regenerating docs/public/reference/openapi.json:

    .venv\Scripts\python.exe docs\tools\generate_api_reference.py
"""
from __future__ import annotations

import html
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
SCHEMA_PATH = DOCS / "public" / "reference" / "openapi.json"
OUT_PATH = DOCS / "public" / "reference" / "api-reference.html"

METHOD_ORDER = ["get", "post", "put", "patch", "delete"]

# Endpoints defined directly in platform/api/app/main.py carry no FastAPI
# tag, but they are exactly what that file's own docstring describes: the
# core of the platform. Grouped under one label rather than left as
# "(untagged)", which would read as an oversight rather than a fact about
# where the code lives.
TAG_LABELS = {
    "(untagged)": "Core (datasets, leases, gates, promotion)",
    "read": "Read models",
    "ingest": "Ingest",
    "agents": "Agents",
    "pipelines": "Pipeline registry",
    "pipeline": "Pipeline runs",
    "external-accounts": "External accounts",
    "auth": "Auth",
    "people": "People and roles",
    "housekeeping": "Storage housekeeping",
    "derivations": "Derived datasets (a query over existing ones)",
    "lifecycle": "Closing an organisation, and legal holds",
    "legal-export": "Legal export (records produced for a legal matter)",
    "iceberg": "Iceberg catalog (for DuckDB, PyIceberg and other standard tools)",
}
TAG_ORDER = list(TAG_LABELS.keys())


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def resolve_ref(schema: dict, ref: str) -> tuple[str, dict]:
    # "#/components/schemas/Foo" -> ("Foo", the schema dict)
    name = ref.rsplit("/", 1)[-1]
    return name, schema.get("components", {}).get("schemas", {}).get(name, {})


def type_label(schema: dict, node: dict) -> str:
    if not node:
        return "any"
    if "$ref" in node:
        name, _ = resolve_ref(schema, node["$ref"])
        return name
    if node.get("anyOf"):
        return " | ".join(type_label(schema, n) for n in node["anyOf"])
    t = node.get("type")
    if t == "array":
        return f"{type_label(schema, node.get('items', {}))}[]"
    if t is None:
        return "any"
    fmt = node.get("format")
    return f"{t} ({fmt})" if fmt else t


def schema_fields_html(schema: dict, node: dict, depth: int = 0) -> str:
    """One field-name/type/required table for an object schema, resolving
    a top-level $ref but not recursing into nested object fields -- those
    are looked up by name in the Data models appendix instead, so this
    stays a table rather than an unreadable tree."""
    if "$ref" in node:
        _, node = resolve_ref(schema, node["$ref"])
    props = node.get("properties")
    if not props:
        return ""
    required = set(node.get("required", []))
    rows = []
    for field, sub in props.items():
        req = "required" if field in required else "optional"
        rows.append(
            f'<tr><td class="mono">{esc(field)}</td>'
            f'<td class="mono">{esc(type_label(schema, sub))}</td>'
            f'<td class="req-{req}">{req}</td></tr>'
        )
    return (
        '<table class="fields"><thead><tr><th>Field</th><th>Type</th>'
        f"<th>&nbsp;</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def render_operation(schema: dict, path: str, method: str, op: dict) -> str:
    op_id = esc(f"{method}-{path}").replace("/", "_").replace("{", "").replace("}", "")
    summary = op.get("summary") or op.get("operationId") or ""
    desc = op.get("description") or ""

    params_html = ""
    params = op.get("parameters", [])
    if params:
        rows = []
        for p in params:
            rows.append(
                f'<tr><td class="mono">{esc(p["name"])}</td>'
                f'<td>{esc(p.get("in", ""))}</td>'
                f'<td class="mono">{esc(type_label(schema, p.get("schema", {})))}</td>'
                f'<td class="req-{"required" if p.get("required") else "optional"}">'
                f'{"required" if p.get("required") else "optional"}</td></tr>'
            )
        params_html = (
            '<div class="block-label">Parameters</div>'
            '<table class="fields"><thead><tr><th>Name</th><th>In</th>'
            f"<th>Type</th><th>&nbsp;</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )

    body_html = ""
    body = op.get("requestBody", {})
    content = body.get("content", {})
    if "application/json" in content:
        node = content["application/json"].get("schema", {})
        body_html = f'<div class="block-label">Request body</div>{schema_fields_html(schema, node)}'
    elif "multipart/form-data" in content:
        node = content["multipart/form-data"].get("schema", {})
        body_html = f'<div class="block-label">Request body (multipart/form-data)</div>{schema_fields_html(schema, node)}'

    responses_html = []
    for status, resp in op.get("responses", {}).items():
        rcontent = resp.get("content", {}).get("application/json", {})
        rschema = rcontent.get("schema", {})
        fields = schema_fields_html(schema, rschema) if rschema else ""
        rtype = type_label(schema, rschema) if rschema else ""
        responses_html.append(
            f'<div class="response"><span class="status status-{status[0]}xx">{esc(status)}</span> '
            f'<span class="resp-desc">{esc(resp.get("description", ""))}</span>'
            + (f' <span class="mono resp-type">&rarr; {esc(rtype)}</span>' if rtype else "")
            + fields
            + "</div>"
        )

    return f"""
<article class="op" id="{op_id}">
  <div class="op-head">
    <span class="method method-{esc(method)}">{esc(method.upper())}</span>
    <span class="path mono">{esc(path)}</span>
  </div>
  <h3>{esc(summary)}</h3>
  {f'<p class="op-desc">{esc(desc)}</p>' if desc else ""}
  {params_html}
  {body_html}
  <div class="block-label">Responses</div>
  {"".join(responses_html)}
</article>
"""


def render_models(schema: dict) -> str:
    schemas = schema.get("components", {}).get("schemas", {})
    cards = []
    for name in sorted(schemas):
        node = schemas[name]
        fields = schema_fields_html(schema, node)
        if not fields:
            continue
        cards.append(f'<article class="model" id="model-{esc(name)}"><h3 class="mono">{esc(name)}</h3>{fields}</article>')
    return "".join(cards)


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    info = schema.get("info", {})

    by_tag: dict[str, list[tuple[str, str, dict]]] = {t: [] for t in TAG_ORDER}
    for path, ops in schema["paths"].items():
        for method in METHOD_ORDER:
            if method not in ops:
                continue
            op = ops[method]
            tag = (op.get("tags") or ["(untagged)"])[0]
            by_tag.setdefault(tag, []).append((path, method, op))

    # A tag with no label above would otherwise vanish from the page without a
    # word, which is how a whole router once went unrendered.
    unlabelled = sorted(t for t in by_tag if t not in TAG_LABELS)
    if unlabelled:
        raise SystemExit(f"tags with no entry in TAG_LABELS: {unlabelled}")

    for items in by_tag.values():
        items.sort(key=lambda t: t[0])

    nav_sections = []
    body_sections = []
    for tag in TAG_ORDER:
        items = by_tag.get(tag, [])
        if not items:
            continue
        label = TAG_LABELS.get(tag, tag)
        nav_links = []
        ops_html = []
        for path, method, op in items:
            op_id = f"{method}-{path}".replace("/", "_").replace("{", "").replace("}", "")
            nav_links.append(
                f'<a href="#{esc(op_id)}"><span class="nav-method nav-method-{esc(method)}">{esc(method.upper())}</span>{esc(path)}</a>'
            )
            ops_html.append(render_operation(schema, path, method, op))
        nav_sections.append(
            f'<div class="nav-group"><div class="nav-group-title">{esc(label)}</div>{"".join(nav_links)}</div>'
        )
        body_sections.append(f'<section class="tag-section"><h2>{esc(label)}</h2>{"".join(ops_html)}</section>')

    models_html = render_models(schema)
    path_count = len(schema.get("paths", {}))
    op_count = sum(len(v) for v in by_tag.values())

    html_out = f"""<title>Munitas: API Reference</title>
<style>
  :root {{
    --bg: #f3f4f7; --bg-raised: #ffffff; --ink: #1a1d29; --ink-soft: #565c6e;
    --ink-faint: #8a90a3; --line: #e0e2ea; --line-strong: #c7cbdb;
    --accent: #3949e0; --accent-bg: #edeefc;
    --get: #0f8a7a; --get-bg: #e5f5f1; --post: #3949e0; --post-bg: #edeefc;
    --delete: #c0392b; --delete-bg: #fbe9e7; --put: #b08a3e; --put-bg: #f5efe0;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #14161f; --bg-raised: #1c1f2b; --ink: #e9eaf2; --ink-soft: #a7acc0;
      --ink-faint: #6f748a; --line: #2b2e3c; --line-strong: #3c3f52;
      --accent: #8b93f7; --accent-bg: #23264a;
      --get: #4fd6bf; --get-bg: #103830; --post: #8b93f7; --post-bg: #23264a;
      --delete: #e08a80; --delete-bg: #3a1f1c; --put: #d8b877; --put-bg: #332a17;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #14161f; --bg-raised: #1c1f2b; --ink: #e9eaf2; --ink-soft: #a7acc0;
    --ink-faint: #6f748a; --line: #2b2e3c; --line-strong: #3c3f52;
    --accent: #8b93f7; --accent-bg: #23264a;
    --get: #4fd6bf; --get-bg: #103830; --post: #8b93f7; --post-bg: #23264a;
    --delete: #e08a80; --delete-bg: #3a1f1c; --put: #d8b877; --put-bg: #332a17;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", "Inter", system-ui, sans-serif;
    line-height: 1.55;
  }}
  .shell {{ display: flex; min-height: 100vh; }}
  nav.rail {{
    width: 300px; flex: none; border-right: 1px solid var(--line);
    padding: 24px 16px; overflow-y: auto; position: sticky; top: 0;
    height: 100vh; background: var(--bg-raised);
  }}
  nav.rail .mark {{
    display: block; font-weight: 700; font-size: 16px; color: var(--ink);
    text-decoration: none; margin-bottom: 4px;
  }}
  nav.rail .sub {{ color: var(--ink-faint); font-size: 12px; margin-bottom: 20px; }}
  .nav-group {{ margin-bottom: 18px; }}
  .nav-group-title {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--ink-faint); font-weight: 700; margin-bottom: 6px;
  }}
  .nav-group a {{
    display: flex; align-items: center; gap: 8px; padding: 4px 6px;
    border-radius: 6px; color: var(--ink-soft); text-decoration: none;
    font-size: 12.5px; font-family: ui-monospace, "SF Mono", Consolas, monospace;
  }}
  .nav-group a:hover {{ background: var(--accent-bg); color: var(--ink); }}
  .nav-method {{
    font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
    font-family: -apple-system, sans-serif; flex: none; width: 42px; text-align: center;
  }}
  .nav-method-get {{ background: var(--get-bg); color: var(--get); }}
  .nav-method-post {{ background: var(--post-bg); color: var(--post); }}
  .nav-method-delete {{ background: var(--delete-bg); color: var(--delete); }}
  .nav-method-put {{ background: var(--put-bg); color: var(--put); }}
  main {{ flex: 1; min-width: 0; padding: 40px 48px 96px; max-width: 900px; }}
  header.hero {{ margin-bottom: 40px; }}
  header.hero .eyebrow {{
    text-transform: uppercase; letter-spacing: 0.12em; font-size: 12px;
    font-weight: 600; color: var(--ink-faint); margin-bottom: 10px;
  }}
  header.hero h1 {{ font-size: 30px; margin: 0 0 12px; letter-spacing: -0.01em; }}
  header.hero p {{ color: var(--ink-soft); max-width: 640px; }}
  .stats {{ display: flex; gap: 24px; margin-top: 20px; }}
  .stats div {{ font-size: 13px; color: var(--ink-faint); }}
  .stats b {{ display: block; font-size: 20px; color: var(--ink); font-weight: 700; }}
  section.tag-section {{ margin-bottom: 48px; }}
  section.tag-section h2 {{
    font-size: 20px; padding-bottom: 12px; border-bottom: 2px solid var(--line-strong);
    margin-bottom: 20px;
  }}
  article.op {{
    background: var(--bg-raised); border: 1px solid var(--line); border-radius: 10px;
    padding: 20px 24px; margin-bottom: 16px; scroll-margin-top: 20px;
  }}
  .op-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .method {{ font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 5px; }}
  .method-get {{ background: var(--get-bg); color: var(--get); }}
  .method-post {{ background: var(--post-bg); color: var(--post); }}
  .method-delete {{ background: var(--delete-bg); color: var(--delete); }}
  .method-put {{ background: var(--put-bg); color: var(--put); }}
  .path {{ font-size: 14px; color: var(--ink-soft); }}
  article.op h3 {{ margin: 0 0 6px; font-size: 16px; }}
  .op-desc {{ color: var(--ink-soft); font-size: 14px; margin: 0 0 12px; }}
  .block-label {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--ink-faint); font-weight: 700; margin: 14px 0 6px;
  }}
  table.fields {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  table.fields th {{
    text-align: left; color: var(--ink-faint); font-weight: 600;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 4px 8px; border-bottom: 1px solid var(--line);
  }}
  table.fields td {{ padding: 5px 8px; border-bottom: 1px solid var(--line); }}
  .mono {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 12.5px; }}
  .req-required {{ color: var(--delete); font-size: 11px; }}
  .req-optional {{ color: var(--ink-faint); font-size: 11px; }}
  .response {{ padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 13px; }}
  .response:last-child {{ border-bottom: none; }}
  .status {{ font-weight: 700; font-size: 12px; padding: 2px 7px; border-radius: 5px; }}
  .status-2xx {{ background: var(--get-bg); color: var(--get); }}
  .status-4xx {{ background: var(--delete-bg); color: var(--delete); }}
  .status-5xx {{ background: var(--delete-bg); color: var(--delete); }}
  .resp-desc {{ color: var(--ink-soft); }}
  .resp-type {{ color: var(--ink-faint); }}
  article.model {{
    background: var(--bg-raised); border: 1px solid var(--line); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 12px; scroll-margin-top: 20px;
  }}
  article.model h3 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); }}
  footer.page {{ margin-top: 64px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--ink-faint); font-size: 12.5px; }}
  footer.page code {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; background: var(--accent-bg); padding: 1px 5px; border-radius: 4px; }}
  @media (max-width: 900px) {{
    .shell {{ flex-direction: column; }}
    nav.rail {{ width: 100%; height: auto; position: static; }}
    main {{ padding: 24px; }}
  }}
</style>

<div class="shell">
  <nav class="rail">
    <a class="mark" href="#top">Munitas<br>API Reference</a>
    <div class="sub">v{esc(info.get("version", ""))} &middot; {op_count} operations</div>
    {"".join(nav_sections)}
    <div class="nav-group">
      <div class="nav-group-title">Data models</div>
      <a href="#models">Appendix &darr;</a>
    </div>
  </nav>
  <main id="top">
    <header class="hero">
      <div class="eyebrow">Munitas &middot; control plane</div>
      <h1>API Reference</h1>
      <p>
        Every route the control plane exposes, generated directly from the
        FastAPI app's own route and Pydantic model definitions
        (<code>platform/api/generate_openapi.py</code>) rather than
        hand-written, so it cannot drift from what the code actually does
        without the generator failing first.
      </p>
      <div class="stats">
        <div><b>{path_count}</b>paths</div>
        <div><b>{op_count}</b>operations</div>
        <div><b>{len(schema.get("components", {}).get("schemas", {}))}</b>data models</div>
      </div>
    </header>

    {"".join(body_sections)}

    <section id="models">
      <h2 style="font-size:20px;padding-bottom:12px;border-bottom:2px solid var(--line-strong);margin-bottom:20px;">
        Data models
      </h2>
      {models_html}
    </section>

    <footer class="page">
      Generated from <code>docs/public/reference/openapi.json</code>, itself generated by
      <code>platform/api/generate_openapi.py</code>. Regenerate both after
      changing a route or a request/response model:
      <code>python platform/api/generate_openapi.py &amp;&amp; python docs/tools/generate_api_reference.py</code>.
      The interactive Swagger UI at <code>/docs</code> on a running instance
      covers the same routes with a "try it" console this static page
      deliberately doesn't attempt.
    </footer>
  </main>
</div>
"""
    OUT_PATH.write_text(html_out, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({op_count} operations, {path_count} paths)")


if __name__ == "__main__":
    main()
