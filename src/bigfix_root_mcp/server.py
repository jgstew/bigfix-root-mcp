"""FastMCP server exposing read-only BigFix root server tools via besapi.

Only read-only tools are registered; no mutating besapi calls exist in this
package (registration is the guard). One documented nuance: submitting a
client query creates a query object server-side, but clients only evaluate
relevance against it, so it is operationally read-only.

Site path rule: this server never uses besapi's mutable "current site path"
connection state (set_current_site_path / get_current_site_path); tools
that need a site take an explicit site_path parameter.
"""

import json
import logging
import sys
from typing import Annotated, Optional

import besapi.besapi

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import Field

from bigfix_root_mcp import __version__, clientquery, connection
from bigfix_root_mcp.errors import (
    bes_errors,
    check_relevance_envelope,
    check_rest_result,
)

logger = logging.getLogger(__name__)

# protect the root server from abusive polling parameters:
MAX_TIMEOUT_SECONDS = 600
MIN_POLL_INTERVAL_SECONDS = 2
API_GET_MAX_TEXT = 50_000

mcp = FastMCP(
    name="bigfix-root-mcp",
    version=__version__,
    instructions=(
        "Read-only access to a HCL BigFix root server REST API. Use "
        "session_relevance_query for data already reported to the server "
        "(fast, no client round-trip). Use the client_query tools to ask "
        "live questions of BigFix agents; those results accumulate over "
        "seconds to minutes as clients report in."
    ),
)


TARGETING_DESCRIPTION = (
    "Targeting: set exactly one of target_all, target_computer_ids, "
    "target_computer_names, or target_relevance (client relevance evaluated "
    "on each agent to decide applicability)."
)


@mcp.tool
@bes_errors("session_relevance_query")
def session_relevance_query(
    relevance: Annotated[
        str,
        Field(description="A BigFix session relevance expression to evaluate."),
    ],
) -> dict:
    """Evaluate a BigFix session relevance query on the root server.

    Session relevance queries data the server already has (computers,
    fixlets, actions, sites, operators...) with no client round-trip.
    Examples: 'number of bes computers', '(name of it, id of it) of bes
    computers whose (now - last report time of it < 1 * day)'.

    There is no server-side result limit, so bound large result sets in the
    relevance itself (e.g. 'firsts 100 of bes computers'). Returns the raw
    JSON envelope: {"result": [...], "evaltime_ms": ...}.
    """
    conn = connection.get_connection()
    envelope = conn.session_relevance_json(relevance)
    return check_relevance_envelope(envelope)


@mcp.tool(description=f"Submit a BigFix client (fast) query. {TARGETING_DESCRIPTION}")
@bes_errors("client_query_submit")
def client_query_submit(
    query_text: Annotated[
        str,
        Field(description="Client relevance to evaluate on each targeted agent."),
    ],
    target_all: Annotated[
        bool, Field(description="Target all computers.")
    ] = False,
    target_computer_ids: Annotated[
        Optional[list[int]], Field(description="Target these BigFix computer IDs.")
    ] = None,
    target_computer_names: Annotated[
        Optional[list[str]], Field(description="Target these computer names.")
    ] = None,
    target_relevance: Annotated[
        Optional[str],
        Field(description="Client relevance targeting expression."),
    ] = None,
) -> dict:
    """Submit a client fast query and return its query_id immediately.

    Results accumulate over seconds to minutes as agents report in; fetch
    them (repeatedly) with client_query_results, or use the client_query
    tool to submit and wait in one call. expected_count is the number of
    targeted computers when knowable, else null.
    """
    conn = connection.get_connection()
    target_xml, expected_count = clientquery.build_target_xml(
        target_all=target_all,
        computer_ids=target_computer_ids,
        computer_names=target_computer_names,
        target_relevance=target_relevance,
    )
    query_id = clientquery.submit_client_query(conn, query_text, target_xml)
    return {"query_id": query_id, "expected_count": expected_count}


@mcp.tool
@bes_errors("client_query_results")
def client_query_results(
    query_id: Annotated[
        int, Field(description="Query ID returned by client_query_submit.")
    ],
) -> dict:
    """Fetch current results for a previously submitted client fast query.

    Results are cumulative and there is no completion flag: safe and cheap
    to call repeatedly until reported_count stops growing or the expected
    number of computers have reported.
    """
    conn = connection.get_connection()
    envelope = clientquery.fetch_client_query_results(conn, query_id)
    return clientquery.summarize_results(envelope, query_id)


@mcp.tool(
    description=(
        "Submit a BigFix client (fast) query and wait for results, polling "
        f"until done or timeout. {TARGETING_DESCRIPTION}"
    )
)
@bes_errors("client_query")
async def client_query(
    query_text: Annotated[
        str,
        Field(description="Client relevance to evaluate on each targeted agent."),
    ],
    ctx: Context,
    target_all: Annotated[
        bool, Field(description="Target all computers.")
    ] = False,
    target_computer_ids: Annotated[
        Optional[list[int]], Field(description="Target these BigFix computer IDs.")
    ] = None,
    target_computer_names: Annotated[
        Optional[list[str]], Field(description="Target these computer names.")
    ] = None,
    target_relevance: Annotated[
        Optional[str],
        Field(description="Client relevance targeting expression."),
    ] = None,
    timeout_seconds: Annotated[
        float, Field(description=f"Max seconds to wait (1-{MAX_TIMEOUT_SECONDS}).")
    ] = 60,
    poll_interval_seconds: Annotated[
        float,
        Field(description=f"Seconds between polls (min {MIN_POLL_INTERVAL_SECONDS})."),
    ] = 5,
    stable_polls: Annotated[
        int,
        Field(
            description=(
                "Stop after this many consecutive polls with no new computers "
                "reporting (once at least one has)."
            )
        ),
    ] = 2,
    expected_count: Annotated[
        Optional[int],
        Field(
            description=(
                "Stop once this many computers reported. Defaults to the "
                "targeted computer count when knowable."
            )
        ),
    ] = None,
) -> dict:
    """Submit a client fast query, poll for results, return them when done.

    Stops when the expected number of computers reported, results stop
    growing, or timeout - stop_reason in the response says which. Partial
    results at timeout are normal (offline agents never report). For very
    long waits, prefer client_query_submit + client_query_results.
    """
    conn = connection.get_connection()
    timeout_seconds = max(1, min(timeout_seconds, MAX_TIMEOUT_SECONDS))
    poll_interval_seconds = max(poll_interval_seconds, MIN_POLL_INTERVAL_SECONDS)

    target_xml, derived_count = clientquery.build_target_xml(
        target_all=target_all,
        computer_ids=target_computer_ids,
        computer_names=target_computer_names,
        target_relevance=target_relevance,
    )
    if expected_count is None:
        expected_count = derived_count

    query_id = clientquery.submit_client_query(conn, query_text, target_xml)
    # progress notifications (not ctx.info logging: the logging capability is
    # deprecated in the 2026-07-28 stateless protocol this server targets)
    await ctx.report_progress(
        progress=0,
        total=expected_count,
        message=f"Submitted client query {query_id}; polling for results.",
    )

    async def progress_cb(reported, expected, message):
        await ctx.report_progress(
            progress=reported, total=expected, message=message
        )

    return await clientquery.poll_client_query(
        conn,
        query_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        stable_polls=stable_polls,
        expected_count=expected_count,
        progress_cb=progress_cb,
    )


@mcp.tool
@bes_errors("get_server_info")
def get_server_info() -> dict:
    """Get BigFix root server info (version, etc) from /api/serverinfo."""
    conn = connection.get_connection()
    result = check_rest_result(conn.get("serverinfo"), "get_server_info")
    return json.loads(result.text)


@mcp.tool
@bes_errors("list_sites")
def list_sites() -> dict:
    """List all sites visible to the configured operator."""
    conn = connection.get_connection()
    result = check_rest_result(conn.get("sites"), "list_sites")
    return result.besdict


@mcp.tool
@bes_errors("get_computer_group")
def get_computer_group(
    group_name: Annotated[str, Field(description="Name of the computer group.")],
    site_path: Annotated[
        str,
        Field(
            description=(
                "Site path containing the group, e.g. 'master', "
                "'custom/MySite', 'operator/SomeOperator'."
            )
        ),
    ],
) -> dict:
    """Look up a computer group by name within an explicit site path.

    Implemented against /api/computergroups/{site_path} directly rather
    than besapi's get_computergroup, which routes through the mutable
    "current site path" connection state this server avoids.
    """
    conn = connection.get_connection()
    result = check_rest_result(
        conn.get(f"computergroups/{site_path}"), "get_computer_group"
    )
    groups = getattr(result.besobj, "ComputerGroup", None)
    if groups is not None:
        for group in groups:
            if group_name == str(group.Name):
                return {
                    "name": group_name,
                    "site_path": site_path,
                    "resource": group.attrib.get("Resource", ""),
                }
    raise ToolError(
        f"Computer group '{group_name}' not found in site '{site_path}'."
    )


@mcp.tool
@bes_errors("get_operator")
def get_operator(
    user_name: Annotated[str, Field(description="BigFix operator user name.")],
) -> dict:
    """Look up a BigFix operator (console user) by name."""
    conn = connection.get_connection()
    user = conn.get_user(user_name)  # RESTResult, or None when not found
    if user is None:
        raise ToolError(f"Operator '{user_name}' not found.")
    return user.besdict


@mcp.tool
@bes_errors("get_dashboard_variable")
def get_dashboard_variable(
    dashboard_name: Annotated[str, Field(description="Dashboard name.")],
    var_name: Annotated[str, Field(description="Dashboard variable name.")],
) -> dict:
    """Read a BigFix dashboard datastore variable value (read-only)."""
    conn = connection.get_connection()
    # besapi returns the value as a plain string
    value = conn.get_dashboard_variable_value(dashboard_name, var_name)
    return {"dashboard": dashboard_name, "name": var_name, "value": value}


@mcp.tool
@bes_errors("whoami")
def whoami() -> dict:
    """Show the configured connection: user, root server, main operator status.

    Cheap connectivity and permission smoke test.
    """
    conn = connection.get_connection()
    return {
        "username": conn.username,
        "rootserver": conn.rootserver,
        "is_main_operator": conn.am_i_main_operator(),
        "besapi_version": besapi.besapi.__version__,
        "bigfix_root_mcp_version": __version__,
    }


@mcp.tool
@bes_errors("api_get")
def api_get(
    path: Annotated[
        str,
        Field(
            description=(
                "Relative REST API path under /api/, e.g. 'help', 'computers', "
                "'sites', 'computer/123'. Query strings allowed."
            )
        ),
    ],
) -> dict:
    """Read-only escape hatch: GET any BigFix REST API path under /api/.

    GET is non-mutating across the BigFix REST API. Use path 'help' to
    discover available endpoints. Response text is truncated to 50KB.
    """
    path = path.strip().lstrip("/")
    if path.startswith("api/"):
        path = path[len("api/") :]
    if "://" in path or path.startswith("..") or not path:
        raise ToolError(
            "path must be a relative BigFix REST API path such as 'help' or "
            "'computers'."
        )
    conn = connection.get_connection()
    result = check_rest_result(conn.get(path), "api_get")
    text = result.text
    truncated = len(text) > API_GET_MAX_TEXT
    return {
        "status_code": result.request.status_code,
        "content_type": result.request.headers.get("content-type", ""),
        "truncated": truncated,
        "text": text[:API_GET_MAX_TEXT],
    }


def main() -> None:
    """Entry point: stderr-only logging, then serve MCP over stdio."""
    # stdout belongs to the MCP stdio transport; all logging (including the
    # besapi logger, which propagates to root) must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
