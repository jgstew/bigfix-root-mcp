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
`bigfix_root_mcp/clientquery.py`, `conn` -> `self`):

- `client_query_submit(query_text, target_xml) -> int` - POST `/api/clientquery`
  with the QueryText XML-escaped (`xml.sax.saxutils.escape`), return
  `int(result.besobj.ClientQuery.ID)`.
- `client_query_results(query_id) -> dict` - GET
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
example loops a fixed 9*20s), no `isatty()` early-break.

## 2. Fix XML escaping in `get_target_xml`

`besapi.besapi.get_target_xml`:

- computer names are interpolated into `<ComputerName>` without XML escaping -
  a name containing `&` or `<` produces malformed XML;
- the relevance branch wraps in `<![CDATA[...]]>` without handling a literal
  `]]>` inside the relevance.

Proposal: use `xml.sax.saxutils.escape()` for names and for relevance (escape
instead of CDATA has no `]]>` edge case). This project's `build_target_xml`
then collapses to a thin call.

**Also: `<AllComputers>` is rejected by the server.** `get_target_xml` returns
`<AllComputers>true</AllComputers>` for the all-computers case, and a BigFix 11
root server answers:

```
400 XML parsing error: no declaration found for element 'AllComputers'
```

`<ComputerID>`, `<ComputerName>` and `<CustomRelevance>` were all accepted in
the same test run, so the element simply is not in the server's schema. The
comment at `besapi.py:180` already notes that
`<AllComputers>false</AllComputers>` "does not work correctly" and substitutes
`<CustomRelevance>False</CustomRelevance>`; the true case needs the same
treatment. Proposal: emit `<CustomRelevance>TRUE</CustomRelevance>`.

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

## 5. `elem2dict` crashes on repeated text elements, and drops attributes

`RESTResult.besdict` is unusable for several common endpoints. Two separate
defects in `besapi.besapi.elem2dict`, both confirmed against a live BigFix 11
root server:

- **Crash.** To promote a scalar to a list on a repeated key it does
  `tempvalue = result[key].copy()`. When the repeated element holds text, that
  value is a `str`, which has no `.copy()`. `GET /api/computer/{id}` returns
  repeated `<Property Name="...">value</Property>` elements, so `besdict`
  raises `AttributeError: 'str' object has no attribute 'copy'` on **any real
  computer record**.
- **Data loss.** Attributes are discarded entirely. For a computer record that
  throws away the `Name` attribute saying which property each value is, and
  for site listings it throws away the `Resource` URL - the only authoritative
  source of a site's REST path.

Proposal: handle the scalar->list promotion without `.copy()` (just
`result[key] = [result[key], value]`), and preserve attributes under `@name`
keys with text under `#text` when an element has both. A reference
implementation is in [`besxml.py`](../src/bigfix_root_mcp/besxml.py); shipping
it upstream lets that module be deleted.

## 6. XML escaping in `set_dashboard_variable_value`

Same defect class as `get_target_xml` (proposal 2): the dashboard name,
variable name and value are all interpolated into the `DashboardData` payload
raw, so any of them containing `&` or `<` produces malformed XML. Proposal:
`xml.sax.saxutils.escape()` each. See
[`writes.build_dashboard_variable_xml`](../src/bigfix_root_mcp/writes.py).

## 7. Action support on `BESConnection`

besapi has no action support at all. The verified surface is small:

- `get_action(action_id)` - GET `/api/action/{id}`
- `get_action_status(action_id)` - GET `/api/action/{id}/status`
- `list_actions()` - GET `/api/actions`
- `stop_action(action_id)` - POST `/api/action/{id}/stop`

Lift from [`actions.py`](../src/bigfix_root_mcp/actions.py) and
[`writes.py`](../src/bigfix_root_mcp/writes.py) (`conn` -> `self`).

## 8. `import_bes_to_site` should accept content, not only a file path

`import_bes_to_site` takes a path, checks `os.access`, and opens the file.
Callers that already hold the XML - an MCP tool, a plugin generating content
in memory - have to write a temp file first. Proposal: accept `bytes`/`str`
content as an alternative, with the existing file path as a thin wrapper over
it. See [`writes.import_bes_content`](../src/bigfix_root_mcp/writes.py).

## 9. Methods that write to stdout

`update_item_from_file` and `export_site_contents` call `print()`, and
`update_item_from_file` is additionally a stub that returns the string
`"WORK IN PROGRESS: besapi.update_item_from_file()"` rather than doing
anything. On any stdio-based host - an MCP server, a BES server plugin whose
stdout is captured - stray stdout corrupts the protocol stream. Proposal:
route all of these through `besapi_logger`, as already proposed for
`get_bes_conn_using_config_file` in proposal 3.
