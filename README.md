# bigfix-root-mcp

A minimal, read-only MCP server around the [besapi](https://github.com/jgstew/besapi)
library, exposing the HCL BigFix root server REST API to MCP clients. Built on
[FastMCP](https://gofastmcp.com) 4 (stateless).

Focus: **session relevance** queries (data the server already has) and
**client fast query** (live questions answered by BigFix agents), plus a few
read-only helpers.

Strongly recommend using the official BigFix Platform MCP server instead: https://help.hcl-software.com/bigfix/11.0/platform/Platform/MCP/c_introduction.html

The capabilities of this MCP server are intentionally limited, where as the official one is not.

## Tools

| Tool | Purpose |
| --- | --- |
| `session_relevance_query` | Evaluate session relevance on the root server; returns the JSON envelope (`result`, `evaltime_ms`). |
| `client_query_submit` | Submit a client fast query, return its `query_id` immediately. |
| `client_query_results` | Fetch current (cumulative) results for a query ID; safe to call repeatedly. |
| `client_query` | Submit + poll in one call with progress notifications; stops on expected count reached, results stable, or timeout. |
| `get_server_info` | Root server version info (`/api/serverinfo`). |
| `list_sites` | Sites visible to the configured operator. |
| `get_computer_group` | Look up a group by name - requires an explicit `site_path`. |
| `get_operator` | Look up a console operator by name. |
| `get_dashboard_variable` | Read a dashboard datastore variable. |
| `whoami` | Configured user/root server, main-operator status, write-gate state; connectivity smoke test. |
| `api_get` | Read-only escape hatch: GET any `/api/` path (try `help` for discovery). |
| `get_computer` | One computer's full record (`/api/computer/{id}`). |
| `find_computers` | Find computers by case-insensitive name substring. |
| `applicable_fixlets` | Content currently relevant to one computer. |
| `get_action` / `get_action_status` | An action's definition, and its per-computer execution state. |
| `list_actions` | Actions visible to the configured operator. |
| `find_content` | Search fixlets/tasks/analyses/baselines by name across sites; resolves the `site_path` needed by `get_content`. |
| `get_content` | One fixlet, task, analysis or baseline by site path and ID. |
| `list_operators` / `list_roles` | Console operators and roles (master operator only). |
| `validate_bes_xml` | Validate BES XML against the BigFix schemas. No server call. |

### Write tools (opt-in)

Absent unless `BIGFIX_ALLOW_WRITES` is set - see [Writes](#writes).

| Tool | Purpose |
| --- | --- |
| `stop_action` | Stop an in-flight action (`POST /api/action/{id}/stop`). |
| `set_dashboard_variable` | Set a dashboard datastore variable. |
| `import_bes_content` | Create/update custom content in a site. Does **not** deploy it. |

### Result bounding

Every tool that can return an unbounded payload is windowed and says so.
List-shaped tools take `limit`/`offset` and report `returned`,
`total_available` and `truncated`; blob-shaped tools report `truncated` and
`total_chars`, and drop an oversized payload rather than cut it into something
that looks complete.

This is not optional politeness: BigFix relevance has **no row-limiting
operator** (`first`, `firsts`, `items`, `elements` are all undefined), so
bounding the response is the only way to bound a result. `find_content` on the
reference deployment matches 12,395 fixlets.

## Resources and prompts

Relevance is the hard part, so the server ships reference material clients can
pull on demand rather than repeating it in every tool description:

| Resource | Contents |
| --- | --- |
| `bigfix://relevance/session-cookbook` | Session relevance that works - every expression verified against a live root server - plus the operators that don't exist. |
| `bigfix://relevance/client-cookbook` | Client (fast query) relevance, targeting forms, reading cumulative results. |
| `bigfix://guide/tools` | Which tool answers which question, how to read bounded responses, what operator scope means. |

Prompts: `diagnose_computer`, `patch_status`, `find_stale_agents`,
`troubleshoot_relevance`.

Relevance errors also carry a cause hint: the server recognizes the common
failure shapes (a non-existent limiting operator, client relevance in a
session query, singular-vs-plural) and appends what to do instead, so a bad
expression is a retry rather than a dead end.

### Client fast query semantics

Client queries are answered by live agents: results accumulate at
`/api/clientqueryresults/{id}` over seconds to minutes as clients report in,
and there is **no completion flag**. The `client_query` tool polls with three
termination heuristics (reported in `stop_reason`):

1. `expected_count_reached` - as many distinct computers reported as targeted;
2. `results_stable` - no new computers for `stable_polls` consecutive polls;
3. `timeout` - partial results at timeout are a *normal* outcome (offline
   agents never report), not an error.

For long waits, use `client_query_submit` then `client_query_results`
repeatedly instead of a single blocking call.

## Configuration

Environment variables win over config files:

| Setting | Env var / `[besapi]` config key | Default |
| --- | --- | --- |
| Root server URL | `BES_ROOT_SERVER` (e.g. `https://bes.example.com:52311`) | - |
| REST operator | `BES_USER_NAME` | - |
| Password | `BES_PASSWORD` | - |
| Write tools | `BIGFIX_ALLOW_WRITES`: `true` to register them | off |
| TLS verification | `BES_SSL_VERIFY`: `false`, `true`, or a CA bundle path | `false` (besapi default) |

Config files are searched in besapi's order: `/etc/besapi.conf`,
`~/besapi.conf`, `~/.besapi.conf`, `./besapi.conf` - same
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

### Operator scope

Every result is limited to what the configured REST operator can see. Only a
**master operator** has full visibility; a regular operator can never be
certain its view is complete, and cannot distinguish "does not exist" from
"outside my scope". So `number of bes computers` returning 35 means *35
computers visible to this operator* - a lower bound, not the BigFix total.

`whoami` reports `is_main_operator` for exactly this reason: check it before
treating any result as the full state of BigFix. The tool descriptions carry
this caveat so LLM clients don't overstate scoped results.

## Safety and design notes

- **Read-only by default**: with `BIGFIX_ALLOW_WRITES` unset, only read tools
  are registered - the write tools do not exist as far as any client can tell.
  One nuance: submitting a client query does create a query object
  server-side, but agents only *evaluate* relevance against it - no
  managed-endpoint state changes.
- **Client fast query is a powerful read.** `client_query` with `target_all`
  evaluates arbitrary client relevance on every agent the operator can see,
  and the BigFix agent runs as SYSTEM/root. That can read file contents,
  registry values and process lists fleet-wide, and the results come back in
  the tool response. Scope the configured operator to the smallest useful set
  of computers; `whoami.is_main_operator` tells you which you have.
- **Explicit site paths**: this server never uses besapi's mutable
  "current site path" connection state (`set_current_site_path` /
  `get_current_site_path` - a bescli convenience); tools that need a site take
  a required `site_path` parameter.
- **Stdout hygiene**: stdout belongs to the MCP stdio transport; all logging
  goes to stderr, and config loading avoids besapi helpers that print.
- **TLS**: verification is off by default to match besapi; set
  `BES_SSL_VERIFY=true` (or a CA bundle path) for anything beyond a lab.
- Generic BigFix logic here is written to be upstreamed into besapi - see
  [docs/besapi-proposals.md](docs/besapi-proposals.md).

## Writes

Set `BIGFIX_ALLOW_WRITES=true` to register the three write tools. The flag
controls *registration*, so with it off there is nothing to call.

Two guardrails apply to all of them:

- **`dry_run` defaults to true.** The response describes the call that would
  be made and nothing is sent. A write only happens on an explicit
  `dry_run=false`.
- **Every attempt is audit-logged** to stderr as one `BIGFIX WRITE` line with
  the operator, target, dry-run flag and outcome.

The set is limited on purpose to operations whose blast radius is reversible
or nil. `import_bes_content` *creates* content; it does not run it - a fixlet
imported this way does nothing until somebody deploys an action against it in
the console.

**Not implemented, and not to be added without their own design round:**
deploying actions (`POST /api/actions`), any `DELETE`, creating sites or
operators, and file upload. Deploying an action is arbitrary code execution as
root across the fleet, which is a different category of risk from anything
here.

## Documentation

| Doc | Contents |
| --- | --- |
| [client-query.md](docs/client-query.md) | Client fast query protocol reference: endpoints, payloads, live-captured result schema, termination heuristics and their tradeoffs. |
| [besapi-notes.md](docs/besapi-notes.md) | besapi behaviors this wrapper depends on or works around (error surfacing, connection lifecycle, return shapes, site-path state). |
| [design-decisions.md](docs/design-decisions.md) | Why the server is shaped this way, plus FastMCP 4 beta specifics. |
| [besapi-proposals.md](docs/besapi-proposals.md) | Proposed upstream besapi changes that would let this project shrink. |
| [rest-endpoints.md](docs/rest-endpoints.md) | Live-verified REST paths, site-path rules, and relevance findings (including the operators that don't exist). |
| [security-review.md](docs/security-review.md) | Threat model and findings for the tool surface. |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Tests run entirely offline against a scripted fake `BESConnection`, including
in-memory end-to-end MCP calls via `fastmcp.Client`.
