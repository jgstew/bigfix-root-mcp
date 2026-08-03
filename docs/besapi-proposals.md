# Proposed besapi changes

This MCP server carries some code that is generic BigFix REST logic, not
MCP-specific, written in an upstreamable shape (standalone functions in
[`clientquery.py`](../src/bigfix_root_mcp/clientquery.py) taking `conn` as the
first argument, no fastmcp imports). Each proposal below would let this
project delete code and shrink to a thinner wrapper. Call sites already
prefer a native besapi method when one exists
(`getattr(conn, "client_query_submit", None)`), so shipping these upstream
requires no coordinated release.

## 1. Client (fast) query support on `BESConnection`

besapi has no clientquery support today; `examples/client_query_from_string.py`
is the only reference. Proposed methods (lift from
`bigfix_root_mcp/clientquery.py`, `conn` → `self`):

- `client_query_submit(query_text, target_xml) -> int` — POST `/api/clientquery`
  with the QueryText XML-escaped (`xml.sax.saxutils.escape`), return
  `int(result.besobj.ClientQuery.ID)`.
- `client_query_results(query_id) -> dict` — GET
  `/api/clientqueryresults/{id}?output=json`, return the parsed JSON envelope
  (key is `"results"`, plural; rows are cumulative; there is no completion flag).
- Optionally a sync `client_query_poll(query_id, timeout_seconds, poll_interval_seconds,
  stable_polls, expected_count)` with the termination heuristics
  (expected-count-reached / results-stable / timeout). The async variant stays
  here since besapi's API is synchronous.
- A `build_target_xml`-style helper or the `get_target_xml` fix below.

Fixes over the example that come along for free: escaped QueryText (the example
interpolates raw relevance into XML), int-coerced query ID (the example keeps
an lxml objectify element), bounded polling with explicit stop reasons (the
example loops a fixed 9×20s), no `isatty()` early-break.

## 2. Fix XML escaping in `get_target_xml`

`besapi.besapi.get_target_xml`:

- computer names are interpolated into `<ComputerName>` without XML escaping —
  a name containing `&` or `<` produces malformed XML;
- the relevance branch wraps in `<![CDATA[...]]>` without handling a literal
  `]]>` inside the relevance.

Proposal: use `xml.sax.saxutils.escape()` for names and for relevance (escape
instead of CDATA has no `]]>` edge case). This project's `build_target_xml`
then collapses to a thin call.

## 3. `get_bes_conn_using_config_file`: logging instead of `print()`, and a `verify` parameter

- The helper `print()`s the config paths it finds. On any stdio-based host
  (MCP servers, BES server plugins capturing stdout) stray stdout corrupts the
  protocol/output stream. Proposal: `besapi_logger.info(...)` instead.
- It also hardcodes `verify=False` with no override. Proposal: accept
  `verify=` and honor a `BES_SSL_VERIFY` key (config file and env var):
  `false` (current default), `true`, or a CA bundle path, passed through to
  `requests`.

Once shipped, `bigfix_root_mcp/connection.py:load_config` shrinks to a call
into besapi.

## 4. A standard way to raise on non-2xx responses

Today only HTTP 403 raises (`PermissionError` inside `RESTResult.__init__`)
and login failures raise `requests.HTTPError`; every other non-2xx comes back
as a `RESTResult` with `valid=False`, so each caller must check
`result.request.status_code`. Proposal: either

- `RESTResult.raise_on_error()` (thin wrapper over
  `self.request.raise_for_status()`), or
- a `check=True` kwarg on `get`/`post`/`put`/`delete`.

This removes per-caller status boilerplate here (`errors.check_rest_result`,
`clientquery._check_status`) and in every besapi plugin.
