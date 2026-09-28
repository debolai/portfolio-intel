"""Numeric grounding check: every number in the answer must appear in a tool output.

A heuristic: it accepts rounding, small integers (counts, list positions) and dates, so it
can miss an invented number that happens to match. It reliably catches the failure that
matters: the model computing or inventing a figure.
"""

import json
import re
from typing import Any

from pydantic import BaseModel

NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
IDENT = re.compile(r"\b[A-Z0-9]+(?:[-.][A-Z0-9]+)*\.[A-Z]{1,2}\b|\b[A-Z]{2}_[A-Z]{2}_\w+\b")


class GroundingReport(BaseModel):
    numbers_in_answer: list[float]
    ungrounded: list[float]


def _numbers(text: str) -> list[float]:
    text = IDENT.sub(" ", DATE.sub(" ", text))  # dates and ids like EQ_EU_PM, 1COV.DE
    return [float(x.replace(",", "")) for x in NUM.findall(text)]


def _walk(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)
    elif isinstance(obj, str):
        out.update(_numbers(obj))


def _ok(x: float, allowed: set[float]) -> bool:
    if abs(x) <= 20 and x.is_integer():  # counts, list positions
        return True
    for a in allowed:
        for v in (a, -a):  # a sign may be stated in words ("underweight by 2.3")
            if abs(x - v) <= max(0.005 * abs(v), 0.006) or x in (round(v, 1), round(v, 2)):
                return True
            if x == round(v):
                return True
    return False


def check_grounding(answer: str, tool_outputs: list[str], question: str) -> GroundingReport:
    # The PM's own numbers, also in the other unit: "50bps" may be restated as "0.5% of NAV".
    q = _numbers(question)
    allowed: set[float] = set(q) | {n / 100 for n in q} | {n * 100 for n in q}
    for t in tool_outputs:
        try:
            _walk(json.loads(t), allowed)
        except json.JSONDecodeError:
            allowed.update(_numbers(t))
    found = _numbers(answer)
    return GroundingReport(
        numbers_in_answer=found, ungrounded=[x for x in found if not _ok(x, allowed)]
    )
