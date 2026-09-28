import json

from portfolio_intel.agent.grounding import check_grounding

TOOL = json.dumps(
    {
        "rows": [{"group": "Banks", "active_weight_pct": 4.9132}],
        "tracking_error_pct": 2.8312,
        "provenance": {"as_of": "2026-09-25"},
    }
)


def test_rounded_tool_numbers_are_grounded():
    ans = "As of 2026-09-25 you are 4.91 percentage points overweight Banks in EQ_EU_PM; TE 2.8%."
    assert check_grounding(ans, [TOOL], "banks?").ungrounded == []


def test_invented_number_is_flagged():
    r = check_grounding("Your TE would fall to 2.41%.", [TOOL], "q")
    assert r.ungrounded == [2.41]


def test_question_numbers_and_unit_conversions_allowed():
    ans = "Trimming HSBA.L by 50bps (0.5% of NAV) reduces the overweight to 4.4132."
    tool = json.dumps({"after": 4.4132})
    assert check_grounding(ans, [tool], "trim HSBC by 50bps").ungrounded == []


def test_non_json_tool_output():
    assert check_grounding("value 12.5", ["Error: limit 12.5"], "q").ungrounded == []
