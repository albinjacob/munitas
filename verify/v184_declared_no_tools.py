r"""U196: a version that declared it uses no tools refuses every tool, in the agent's own code.

Run on the host, where the agent package is installed (it is not in the API image):

    .venv\Scripts\python.exe verify\v184_declared_no_tools.py

An empty list of tools used to mean "nothing was said" and was treated as unrestricted, so an agent that truly uses no tools could not be told apart from one whose owner never said. The
owner now says which: with tools declared, the tool layer refuses anything outside the list, including everything when the list is empty; with tools not declared, nothing limits it and the
registry flags it. This proves the layer's answer for each of the four cases, with the control plane's credential answer replaced by a grant, so it tests the check and nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "verify"))

from agent import tools as agent_tools  # noqa: E402
from agent.identity import AgentIdentity, Budget  # noqa: E402
from common import check, heading, summary  # noqa: E402


class _Grant:
    status_code = 200

    def json(self):
        return {"reasons": []}


def answer(declared: bool, scope: frozenset, tool: str = "read_dataset_version") -> str:
    """What the tool layer says to one call: 'allowed', or the refusal."""
    identity = AgentIdentity(principal="p", tenant="canary", roles=("agent_runtime",), purpose="u196", agent_version_id="v", tool_scope=scope, tools_declared=declared)
    ctx = agent_tools.ToolContext(identity=identity, run_id="r", budget=Budget())
    with mock.patch.object(agent_tools.httpx, "post", return_value=_Grant()), mock.patch.object(agent_tools, "record_decision"):
        try:
            agent_tools._decide(ctx, tool, "version", mock.MagicMock())
            return "allowed"
        except agent_tools.ToolDenied as exc:
            return str(exc)


def main() -> int:
    heading("U196: what the tool layer says for each case")
    nothing = answer(True, frozenset())
    check("declared with no tools: a tool is refused, and the reason says the version declared it uses none", "declared that it uses no tools" in nothing and nothing.startswith("read_dataset_version denied"), nothing)
    check("declared with no tools: every other tool is refused too", all(answer(True, frozenset(), t).endswith("uses no tools") for t in ("summarise_counts", "list_review_queue", "fetch_url")))
    other = answer(True, frozenset({"summarise_counts"}))
    check("declared with other tools: a tool outside the list is refused, naming the scope", "outside the tool scope" in other, other)
    check("declared with the tool in the list: it is allowed", answer(True, frozenset({"read_dataset_version"})) == "allowed")
    check("not declared: nothing limits the tools", answer(False, frozenset()) == "allowed")
    check("not declared but a list given (an old version): the list still limits", "outside the tool scope" in answer(False, frozenset({"summarise_counts"})))
    check("an identity made without the new field behaves as not declared, so older callers are unchanged", AgentIdentity(principal="p", tenant="t", roles=(), purpose="x", agent_version_id="v").tools_declared is False)
    return summary("U196")


if __name__ == "__main__":
    sys.exit(main())
