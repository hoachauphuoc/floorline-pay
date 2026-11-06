# Floorline Pay for an outside agent

An agent that pays carriers should be able to ask first. Floorline Pay offers that through the Model Context Protocol, with tools that
**only read**. Money still moves in one place: the settler, after a person's release.

## The tools

| Tool | Answers |
|---|---|
| `check_payout_risk(shipment)` | the deterministic findings for payout details (payee not the one on file, payout above the booked freight, payee changed recently) and the risk floor they set. Local, no network |
| `get_payment_state(case_id)` | what the settler did with one case: awaiting a decision, paid, returned, held or failed, and why |
| `get_payment_risk(case_id)` | the findings of one case, the analyst's reading, the carrier's credit standing, and whether a second approver is needed |
| `list_frozen_parties()` | the carriers and shippers whose payments are frozen, with the reason |
| `replay_attacks()` | the attacks Floorline is built to stop, replayed on a routine payout: the layer that stops each and the money that moved (none) |

There is no tool that captures, pays, voids, releases, approves, lifts a freeze, pauses payments or books. `tests/test_mcp_server.py` pins the
list of five and fails if a name suggesting any of those appears. The Floorline key stays in the server process; the agent never sees it.

## Run it

```bash
pip install -r requirements.txt
FLOORLINE_API_URL=https://<your service> FLOORLINE_API_KEY=<the service's VF_API_KEY> python -m vf_logistics.mcp_server
```

In Claude Desktop (`claude_desktop_config.json`); `PYTHONPATH` must be the absolute path of this repository's `src` folder, because the client does not start in the repository:

```json
{
  "mcpServers": {
    "floorline-pay": {
      "command": "python",
      "args": ["-m", "vf_logistics.mcp_server"],
      "env": {
        "PYTHONPATH": "<path to this repository>/src",
        "FLOORLINE_API_URL": "https://<your service>",
        "FLOORLINE_API_KEY": "<the service's VF_API_KEY>"
      }
    }
  }
}
```

The server reads through the same API the console uses and holds that API's key, so it is read-only because it only calls the five routes
above, not because the key is limited. Give an agent this server, not the key.

## Next to PayPal's own MCP server

PayPal publishes an MCP server of its own (`npx @paypal/mcp`, sandbox with `PAYPAL_ENVIRONMENT=SANDBOX`). It can act on a PayPal account.
Run it beside Floorline's, and pass `--tools` only the tools you want an agent to have (its documentation lists the catalog), so that the
agent can look at PayPal and ask Floorline, and cannot pay.

```json
{
  "mcpServers": {
    "floorline-pay": { "command": "python", "args": ["-m", "vf_logistics.mcp_server"], "env": { "PYTHONPATH": "<path to this repository>/src", "FLOORLINE_API_URL": "https://<your service>", "FLOORLINE_API_KEY": "<key>" } },
    "paypal": { "command": "npx", "args": ["-y", "@paypal/mcp", "--tools=<read-only tools you choose>"], "env": { "PAYPAL_ACCESS_TOKEN": "<sandbox token>", "PAYPAL_ENVIRONMENT": "SANDBOX" } }
  }
}
```

This has not been run end to end: only Floorline's server is tested here. PayPal's `paypal-agent-toolkit` Python package was not added to the
project: version 1.11.0 requires `crewai-tools==0.13.2`, which cannot be installed, and it would pull in two agent frameworks the project
does not use.
