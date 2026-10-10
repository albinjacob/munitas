"""Find every setting the platform and the worker read, and the default each one has in the code.

This is the independent half of the settings documentation. The catalogue (`settings_reference.json`) says what each setting means; this reads the
code and says what the code actually does, so a check can compare the two. Neither is derived from the other.

A setting is read in one of these ways: `os.environ.get("NAME", default)`, `os.environ["NAME"]`, `os.getenv("NAME", default)`, or the platform's own
`_required("NAME")`; and Docker Compose reads `${NAME}` and `${NAME:-default}` in `docker-compose.yml`. Scripts for verification (`verify/`) and the
tests read some for their own purposes; those are not settings of the platform and are not scanned.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CODE = ("platform", "worker")
NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _evaluate(node: ast.AST):
    """A constant, or plain arithmetic on constants (`32 * 1024 * 1024`, `str(2 * 1024 ** 3)`); raises ValueError for anything else."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        ops = {ast.Add: lambda x, y: x + y, ast.Sub: lambda x, y: x - y, ast.Mult: lambda x, y: x * y,
               ast.Pow: lambda x, y: x ** y, ast.FloorDiv: lambda x, y: x // y}
        for kind, fn in ops.items():
            if isinstance(node.op, kind):
                return fn(left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("str", "int") and len(node.args) == 1:
        value = _evaluate(node.args[0])
        return str(value) if node.func.id == "str" else int(value)
    raise ValueError("not a constant")


def _default_of(node: ast.AST | None) -> str | None:
    """The default as text, when it is a plain value; "(computed)" when the code works it out; None when there is none."""
    if node is None:
        return None
    try:
        return str(_evaluate(node))
    except Exception:  # noqa: BLE001
        return "(computed)"


def scan_code() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for base in CODE:
        for path in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                name = default = None
                required = False
                if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    func = ast.unparse(node.func)
                    if func in ("os.environ.get", "os.getenv", "environ.get"):
                        name = node.args[0].value
                        default = _default_of(node.args[1]) if len(node.args) > 1 else None
                    elif func in ("_required", "config._required"):
                        name, required = node.args[0].value, True
                if isinstance(node, ast.Call) and ast.unparse(node.func) in ("_pair", "config._pair") and len(node.args) == 3 and                         isinstance(node.args[0], ast.Constant):
                    where = path.relative_to(ROOT).as_posix()
                    for suffix, arg in (("_KEY", node.args[1]), ("_SECRET", node.args[2])):
                        entry = found.setdefault(node.args[0].value + suffix, {"default": None, "defaults": [], "required": False, "read_in": []})
                        if where not in entry["read_in"]:
                            entry["read_in"].append(where)
                        entry["default"] = entry["default"] or _default_of(arg)
                        if _default_of(arg) not in entry["defaults"]:
                            entry["defaults"].append(_default_of(arg))
                elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in ("os.environ", "environ") and isinstance(node.slice, ast.Constant):
                    name, required = node.slice.value, True
                if not name or not NAME.match(name):
                    continue
                entry = found.setdefault(name, {"default": None, "defaults": [], "required": False, "read_in": []})
                where = path.relative_to(ROOT).as_posix()
                if where not in entry["read_in"]:
                    entry["read_in"].append(where)
                if required:
                    entry["required"] = True
                if default is not None and entry["default"] is None:
                    entry["default"] = default
                if default is not None and default not in entry["defaults"]:
                    entry["defaults"].append(default)
    return found


def scan_compose() -> dict[str, dict]:
    """`${NAME}`, `${NAME:-default}` and `${NAME:?message}` in docker-compose.yml. Only `-` and `:-` give a default: `?` says the setting is required, and
    what follows it is the message shown when it is missing, not a value."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    found: dict[str, dict] = {}
    for m in re.finditer(r"\$\{([A-Z][A-Z0-9_]+)(?:(:?-|:?\?)([^}]*))?\}", text):
        entry = found.setdefault(m.group(1), {"default": None, "defaults": [], "required": False})
        if m.group(2) and m.group(2).endswith("?"):
            entry["required"] = True
        elif m.group(2) and m.group(3) is not None:
            if entry["default"] is None:
                entry["default"] = m.group(3)
            if m.group(3) not in entry["defaults"]:
                entry["defaults"].append(m.group(3))
    return found


def scan() -> dict[str, dict]:
    """Every setting, with where it is read and the default the code gives it."""
    code, compose = scan_code(), scan_compose()
    out: dict[str, dict] = {}
    for name in sorted(set(code) | set(compose)):
        c, k = code.get(name), compose.get(name)
        out[name] = {
            "default": (c or {}).get("default") if (c or {}).get("default") is not None else (k or {}).get("default"),
            "defaults": list(dict.fromkeys(((c or {}).get("defaults") or []) + ((k or {}).get("defaults") or []))),
            "required": bool((c or {}).get("required")) or bool((k or {}).get("required")),
            "read_in": (c or {}).get("read_in", []) + (["docker-compose.yml"] if k else []),
        }
    return out


def worker_names() -> list[str]:
    """The settings the host worker reads from `.env`, and only those.

    `.env` also holds secrets the worker must not hold (the storage administrator's secret, the master key), so the start script gives the worker
    exactly this list and nothing else from the file. Settings the platform itself passes to the containers it starts are left out as well:
    they are made per job and never belong in `.env`.
    """
    import json
    catalogue = json.loads((ROOT / "docs" / "tools" / "settings_reference.json").read_text(encoding="utf-8"))
    internal = {entry["name"] for entry in catalogue["settings"] if (entry.get("kind") or _group_kind(catalogue, entry["group"])) == "internal"}
    return sorted(name for name, entry in scan().items()
                  if any(path.startswith("worker/") for path in entry["read_in"]) and name not in internal)


def _group_kind(catalogue: dict, group: str) -> str:
    return next((g["kind"] for g in catalogue["groups"] if g["id"] == group), "setting")


if __name__ == "__main__":
    import json
    import sys
    if "--worker-names" in sys.argv:
        print(chr(10).join(worker_names()))
    else:
        print(json.dumps(scan(), indent=2))
