"""`pi-replay <event_id>`: re-run an audited tool call and verify its result hash."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from portfolio_intel.analytics.whatif import Trade
from portfolio_intel.config import settings

from . import server
from .audit import result_hash
from .guardrails import current_principal


def find_event(path: Path, event_id: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            rec: dict[str, Any] = json.loads(line)
            if rec.get("event_id") == event_id:
                return rec
    raise SystemExit(f"event {event_id} not found in {path}")


def replay(rec: dict[str, Any]) -> tuple[bool, str]:
    if rec["status"] != "ok":
        return False, f"original call failed ({rec['error']}); nothing to verify"
    snapshot = server.svc().snapshot_id
    if snapshot != rec["data_snapshot_id"]:
        return False, (
            f"loaded snapshot {snapshot} differs from the audited {rec['data_snapshot_id']}; "
            "point PI_DUCKDB_PATH at the original snapshot"
        )
    tool = getattr(server, rec["tool"]).__wrapped__  # bypass the audit decorator
    args = dict(rec["args"])
    if "trades" in args:
        args["trades"] = [Trade(**t) for t in args["trades"]]
    token = current_principal.set(rec["principal"])
    try:
        result = tool(**args)
    finally:
        current_principal.reset(token)
    ok = result_hash(result) == rec["result_sha256"]
    calc = result.provenance.calc_id
    return ok, f"calc_id {calc} ({'matches' if calc == rec['calc_id'] else 'DIFFERS'})"


def main() -> None:
    ap = argparse.ArgumentParser(prog="pi-replay")
    ap.add_argument("event_id")
    ap.add_argument("--audit", type=Path, default=Path(settings.audit_path))
    args = ap.parse_args()
    rec = find_event(args.audit, args.event_id)
    ok, detail = replay(rec)
    print(f"{rec['tool']} {json.dumps(rec['args'])}")
    print(
        f"{'VERIFIED' if ok else 'MISMATCH'}: result_sha256 {rec['result_sha256'][:16]}…, {detail}"
    )
    sys.exit(0 if ok else 1)
