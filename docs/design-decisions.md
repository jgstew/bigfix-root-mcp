# Design decisions

Why this server is shaped the way it is. Recorded so these don't get
re-litigated or "cleaned up" into problems later.

## Read-only by construction, not by flag

The registered tool list *is* the security boundary: no mutating besapi call
(`post` to a mutating endpoint, `put`, `delete`, `import_bes_to_site`,
`upload`, `create_*`, `set_dashboard_variable_value`, or the `export_*` helpers
that write to local disk) appears anywhere in the package.

An `ALLOW_WRITES`-style env flag was considered and rejected: a flag guarding
zero tools is dead configuration that implies a capability that doesn't exist.
If write support is ever added, the flag should arrive *with* it.

One honest caveat: `client_query_submit` does create a query object on the root
server. It is read-only in the sense that matters — agents only evaluate
relevance, nothing on managed endpoints changes — but it is not a pure GET, and
the tool description says so rather than hiding it.

`api_get` is deliberately GET-only. GET is non-mutating across the BigFix REST
API, which makes a generic escape hatch safe and removes the pressure to add
one-off tools for every endpoint.

## Explicit site paths everywhere

Tools that need a site take a required `site_path` parameter. Beyond the
correctness argument (see [besapi-notes.md](besapi-notes.md#site-path-state)),
this matters specifically for MCP: an LLM issues tool calls in an order nobody
audited, and a hidden "current site" would make the same call return different
results depending on history. Explicit parameters make every call reproducible
in isolation.

## Lazy connection, not a FastMCP lifespan

The connection is a lazily-created module-level singleton rather than something
established in the server lifespan.

Rationale: `BESConnection.__init__` performs a login round-trip. In a lifespan,
an unreachable or misconfigured root server means the server fails to start and
the client sees a cryptic startup failure with no tool list. Lazily, the server
always starts, tools are always discoverable, and a connection problem surfaces
as a clear `ToolError` on the tool that needed it.

The singleton (rather than per-call connections) avoids a login round-trip per
tool call, and is required anyway because besapi's `__del__` closes the session
of a garbage-collected connection.

## Escaping over CDATA

Client query text and computer names are escaped with
`xml.sax.saxutils.escape`, not wrapped in `<![CDATA[...]]>`. CDATA looks
convenient for relevance (which is full of quotes and operators) but breaks on
a literal `]]>` in the payload. Escaping has no such edge case. This also
differs from `besapi.get_target_xml`, which CDATA-wraps relevance and does not
escape names at all.

## Upstreamable shape

`clientquery.py` imports nothing from fastmcp or from the rest of this package,
takes `conn` as its first argument, and raises besapi-style exceptions
(`ValueError`, `requests.HTTPError`) rather than `ToolError`. Lifting any of it
into `besapi.besapi.BESConnection` is a mechanical `conn` → `self` change.

Call sites dispatch through `getattr(conn, "client_query_submit", None)` first,
so if besapi ships native methods, this server prefers them immediately with no
code change; a later release just deletes the local copies and raises the besapi
minimum. MCP concerns (ToolError translation, progress reporting, tool schemas)
stay in `server.py`/`errors.py` and never leak into the upstreamable layer.

The async `poll_client_query` is the one piece deliberately *not* an upstream
candidate — besapi's API is synchronous, and asyncio doesn't belong in it.

## FastMCP 4 notes

Pinned to `fastmcp==4.0.0b1` exactly, per the release's own advice ("pin an
exact version and expect sharp edges").

- **The `[tool.uv]` block is a resolver constraint, not a dependency.** The
  dependency is `fastmcp`; in 4.0 that is a meta-package whose code lives in
  `fastmcp-slim`. uv only permits pre-releases for explicitly named packages,
  so without `constraint-dependencies = ["fastmcp-slim==4.0.0b1"]` a `uv lock`
  can fail on the transitive prerelease. Irrelevant to pip.
- **No `ctx.info()` logging.** The MCP logging capability is deprecated as of
  the `2026-07-28` stateless protocol era (SEP-2577) and emits a deprecation
  warning. `client_query` reports exclusively via `ctx.report_progress`, which
  is supported on every protocol era.
- **`mcp.run(show_banner=False)`** is required. The default banner, plus a PyPI
  version check, writes to stderr on startup — harmless to the protocol but
  noisy in client logs.
- Do not design around `ctx.elicit()`, `ctx.sample()`, or `ctx.list_roots()`:
  the latter two are removed in 4.0, and elicitation raises on modern
  stateless connections.
- `ctx.set_state()` is request-scoped on modern connections and does not
  persist between tool calls. Nothing here relies on cross-call context state.
- Falling back to `fastmcp>=3.4.5,<4` (the stable line) would be cheap if the
  beta proves troublesome — the server-side API used here is unchanged between
  3.x and 4.x. The exact pin plus in-memory `Client` tests are the safety net
  against beta API drift.

## Testing without BigFix

All 53 tests run offline against a scripted `FakeBESConnection`
(`tests/conftest.py`) that mimics besapi's `url()`, queues responses per verb,
and records calls. Tool-level tests drive the real server through an in-memory
`fastmcp.Client`, so tool registration, schema generation, and `ToolError`
propagation are all exercised — not just the underlying functions.

Two things the fakes deliberately do **not** abstract away: `RESTResult.besobj`
is real `lxml.objectify` parsing of real XML strings, and status codes go
through a `raise_for_status` that behaves like `requests`. Both are where the
integration bugs actually live.

The parts that can only be confirmed against a real root server — the
`clientqueryresults` row schema and relevance encoding round-tripping — were
verified live and the findings recorded in
[client-query.md](client-query.md) and
[besapi-notes.md](besapi-notes.md).
