# Responsible AI controls

The design principle is **read-only by construction, not by prompt**. Controls that depend on
the model behaving are a second line of defence, never the only one.

| Layer | Control | Where |
|---|---|---|
| Capability | No write, order or execution tools exist. The database is opened `read_only=True`. Only the ingest job (a separate IAM role) writes data. | `mcp_server/server.py`, `data/store.py`, Terraform IAM |
| Input | Pydantic types and enums on every argument; bounds (`±500bps` per trade, `≤ 20` trades, `top_n ≤ 50`, `0 < nav ≤ 1e12`); a per-principal portfolio allow-list (benchmarks are public). | tool signatures, `mcp_server/guardrails.py`, `analytics/whatif.py` |
| Output | Every number is in display units with a `unit` field; simulations carry `hypothetical: true` and "No order has been created"; risk responses warn when coverage < 95%. | `analytics/models.py`, `service.py` |
| Model behaviour | System prompt forbids computing numbers and placing orders; a post-generation numeric grounding check flags any number not found in a tool output and asks the model to rewrite once. | `agent/prompts.py`, `agent/grounding.py`, `agent/loop.py` |
| Access | Bearer token per principal (OAuth/SSO in production); WAF rate limiting; services in private subnets. | `mcp_server/auth.py`, Terraform |
| Accountability | Every tool call is appended to an audit log (args, result hash, `calc_id`, snapshot, principal, `trace_id`); in AWS via Firehose to S3 Object Lock (WORM). `pi-replay <event_id>` re-runs a call and verifies the result hash. | `mcp_server/audit.py`, `mcp_server/replay.py`, Terraform |
| Transparency | `get_methodology` tool and `methodology://risk` resource; provenance (`as_of`, `data_snapshot_id`, `methodology_version`, `calc_id`) on every response; documented limitations. | `methodology.md`, `docs/limitations.md` |

## Error handling

Expected errors (unknown portfolio, entitlement, long-only violation, insufficient cash, a bond
portfolio passed to an equity-only tool) are returned to the client as tool errors with their
message, so the agent can recover, for example by switching to `funding="pro_rata"`.
Unexpected exceptions are masked from the client and recorded in the audit log.

## What the agent does when asked to trade

"Sell 50bps of HSBC now" cannot be executed: no tool exists that could do it. The system prompt
tells the model to say so and offer a simulation instead, and the eval set checks for exactly
this refusal (`refuse_execution`).
