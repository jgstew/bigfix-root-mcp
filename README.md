# bigfix-root-mcp

A minimal, read-only MCP server around the [besapi](https://github.com/jgstew/besapi)
library, exposing the HCL BigFix root server REST API to MCP clients. Built on
[FastMCP](https://gofastmcp.com) 4 (stateless).

Focus: **session relevance** queries (data the server already has) and
**client fast query** (live questions answered by BigFix agents), plus a few
read-only helpers.

## Tools

| Tool | Purpose |
| --- | --- |
| `session_relevance_query` | Evaluate session relevance on the root server; returns the JSON envelope (`result`, `evaltime_ms`). |
| `client_query_submit` | Submit a client fast query, return its `query_id` immediately. |
| `client_query_results` | Fetch current (cumulative) results for a query ID; safe to call repeatedly. |
| `client_query` | Submit + poll in one call with progress notifications; stops on expected count reached, results stable, or timeout. |
| `get_server_info` | Root server version info (`/api/serverinfo`). |
| `list_sites` | Sites visible to the configured operator. |
| `get_computer_group` | Look up a group by name — requires an explicit `site_path`. |
| `get_operator` | Look up a console operator by name. |
| `get_dashboard_variable` | Read a dashboard datastore variable. |
| `whoami` | Configured user/root server, main-operator status; connectivity smoke test. |
| `api_get` | Read-only escape hatch: GET any `/api/` path (try `help` for discovery). |

### Client fast query semantics

Client queries are answered by live agents: results accumulate at
`/api/clientqueryresults/{id}` over seconds to minutes as clients report in,
and there is **no completion flag**. The `client_query` tool polls with three
termination heuristics (reported in `stop_reason`):

1. `expected_count_reached` — as many distinct computers reported as targeted;
2. `results_stable` — no new computers for `stable_polls` consecutive polls;
3. `timeout` — partial results at timeout are a *normal* outcome (offline
   agents never report), not an error.

For long waits, use `client_query_submit` then `client_query_results`
repeatedly instead of a single blocking call.

## Configuration

Environment variables win over config files:

| Setting | Env var / `[besapi]` config key | Default |
| --- | --- | --- |
| Root server URL | `BES_ROOT_SERVER` (e.g. `https://bes.example.com:52311`) | — |
| REST operator | `BES_USER_NAME` | — |
| Password | `BES_PASSWORD` | — |
| TLS verification | `BES_SSL_VERIFY`: `false`, `true`, or a CA bundle path | `false` (besapi default) |

Config files are searched in besapi's order: `/etc/besapi.conf`,
`~/besapi.conf`, `~/.besapi.conf`, `./besapi.conf` — same
`[besapi]` section format as besapi/bescli, so an existing config just works.
Prefer keeping credentials in `~/besapi.conf` over MCP client config files.

Example MCP client config (see [.mcp.json](.mcp.json)):

```json
{
  "mcpServers": {
    "bigfix-root": {
      "command": "uvx",
      "args": ["bigfix-root-mcp"]
    }
  }
}
```

## Install / run

```bash
pip install bigfix-root-mcp   # or: uvx bigfix-root-mcp
```

From a checkout:

```bash
pip install -e ".[dev]"
bigfix-root-mcp               # or: python -m bigfix_root_mcp
```

Smoke test against a live root server with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector bigfix-root-mcp
```

then call `whoami`, `session_relevance_query` with `number of bes computers`,
and `client_query` targeting a known computer ID.

## Safety and design notes

- **Read-only surface**: only the tools above are registered; no mutating
  besapi calls exist in this package. One nuance: submitting a client query
  does create a query object server-side, but agents only *evaluate* relevance
  against it — no managed-endpoint state changes. Any future write support
  would be opt-in via an explicit environment flag.
- **Explicit site paths**: this server never uses besapi's mutable
  "current site path" connection state (`set_current_site_path` /
  `get_current_site_path` — a bescli convenience); tools that need a site take
  a required `site_path` parameter.
- **Stdout hygiene**: stdout belongs to the MCP stdio transport; all logging
  goes to stderr, and config loading avoids besapi helpers that print.
- **TLS**: verification is off by default to match besapi; set
  `BES_SSL_VERIFY=true` (or a CA bundle path) for anything beyond a lab.
- Generic BigFix logic here is written to be upstreamed into besapi — see
  [docs/besapi-proposals.md](docs/besapi-proposals.md).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Tests run entirely offline against a scripted fake `BESConnection`, including
in-memory end-to-end MCP calls via `fastmcp.Client`.
