"""Golden PM questions through the real model, against the running stack.

    make eval                                   # local Compose stack (MCP at PI_MCP_URL)
    PI_API_URL=https://… uv run pytest -m eval  # a deployed environment, via /v1/ask

Calls the Anthropic API: needs ANTHROPIC_API_KEY. Scores are also sent to Langfuse when
LANGFUSE_* keys are set.
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from portfolio_intel.agent.loop import ask, mcp_client
from portfolio_intel.config import settings

pytestmark = pytest.mark.eval
CASES: list[dict[str, Any]] = yaml.safe_load((Path(__file__).parent / "golden.yaml").read_text())
PASS_RATE = float(os.environ.get("PI_EVAL_PASS_RATE", "0.9"))
RUN_ID = os.environ.get("PI_EVAL_RUN_ID", uuid.uuid4().hex[:8])


def dig(obj: Any, path: str) -> Any:
    """'rows[group=Banks].active_weight_pct', 'rows[0].pct_of_te', 'trades[0].delta_weight_bps'."""
    for part in path.split("."):
        m = re.fullmatch(r"(\w+)(?:\[(.+)])?", part)
        if not m:
            raise ValueError(part)
        obj = obj[m.group(1)]
        sel = m.group(2)
        if sel is None:
            continue
        if sel.isdigit():
            obj = obj[int(sel)]
        else:
            k, v = sel.split("=")
            obj = next(x for x in obj if str(x[k]) == v)
    return obj


def number_in(answer: str, ref: float) -> bool:
    text = answer.replace(",", "")
    candidates = {f"{ref:.{d}f}" for d in (0, 1, 2, 3, 4)} | {f"{abs(ref):.{d}f}" for d in (1, 2)}
    return any(re.search(rf"(?<![\d.]){re.escape(c)}(?!\d)", text) for c in candidates)


async def reference(spec: dict[str, Any]) -> float:
    async with mcp_client() as c:
        r = await c.call_tool(spec["tool"], spec["args"])
    assert not r.is_error, r.content
    return float(dig(r.structured_content, spec["path"]))


async def run_agent(question: str) -> dict[str, Any]:
    api = os.environ.get("PI_API_URL")
    if not api:
        return await ask(question)
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            f"{api}/v1/ask",
            json={"question": question},
            headers={"Authorization": f"Bearer {settings.mcp_token}"},
        )
        r.raise_for_status()
        out: dict[str, Any] = r.json()
        return out


def score(case: dict[str, Any], out: dict[str, Any], ref: float | None) -> dict[str, bool]:
    calls = out["tool_calls"]
    names = [c["name"] for c in calls]
    answer = out["answer"].casefold()
    s: dict[str, bool] = {"grounded": out["grounding"]["ungrounded"] == []}
    if "expect_tools" in case:
        s["tools"] = all(t in names for t in case["expect_tools"])
    if "expect_any_tool" in case:
        s["any_tool"] = any(t in names for t in case["expect_any_tool"])
    if "expect_args" in case:
        want = case["expect_args"]

        def matches(inp: dict[str, Any]) -> bool:
            try:
                return all(dig(inp, k) == v for k, v in want.items())
            except (KeyError, IndexError, StopIteration, TypeError):
                return False

        s["args"] = any(matches(c["input"]) for c in calls)
    if "expect_no_tools_named" in case:
        s["no_forbidden_tool"] = not any(
            bad in n for n in names for bad in case["expect_no_tools_named"]
        )
    if "expect_answer_contains_any" in case:
        s["answer_text"] = any(p.casefold() in answer for p in case["expect_answer_contains_any"])
    if ref is not None:
        s["reference_number"] = number_in(out["answer"], ref)
    return s


def log_to_langfuse(case_id: str, scores: dict[str, bool]) -> None:
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return
    from langfuse import get_client

    lf = get_client()
    for name, ok in scores.items():
        lf.create_score(
            name=f"eval.{name}", value=float(ok), data_type="NUMERIC",
            session_id=f"eval-{RUN_ID}", comment=case_id,
        )  # fmt: skip
    lf.flush()


def test_golden_set() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    async def run_all() -> list[tuple[str, dict[str, bool], str]]:
        rows = []
        for case in CASES:
            ref = await reference(case["reference"]) if "reference" in case else None
            try:
                out = await run_agent(case["question"])
                scores = score(case, out, ref)
                answer = out["answer"]
            except Exception as e:  # a crash is a failed case, not an aborted run
                scores, answer = {"ran": False}, repr(e)
            log_to_langfuse(case["id"], scores)
            rows.append((case["id"], scores, answer))
        return rows

    rows = asyncio.run(run_all())
    passed = [cid for cid, s, _ in rows if all(s.values())]
    report = {cid: {k: v for k, v in s.items() if not v} for cid, s, _ in rows}
    print(json.dumps({"run_id": RUN_ID, "passed": len(passed), "total": len(rows),
                      "failures": {k: v for k, v in report.items() if v}}, indent=2))  # fmt: skip
    for cid, s, answer in rows:
        if not all(s.values()):
            print(f"\n--- {cid}\n{answer[:800]}")
    assert len(passed) / len(rows) >= PASS_RATE, f"pass rate {len(passed)}/{len(rows)}"
