"""The small, gated write surface.

Scope rule: only operations whose blast radius is reversible or nil.

  stop_action           POST /api/action/{id}/stop     - the undo
  set_dashboard_variable POST /api/dashboardvariable/{d}/{n} - datastore only
  import_bes_content    POST /api/import/{site}        - creates content

Creating content is not the same as running it: an imported fixlet does
nothing until somebody takes an action on it in the console. That separation
is why import is in here and deploying an action is not.

Deliberately absent, and not to be added without their own design round:
  POST   /api/actions        - deploy; arbitrary code as root, fleet-wide
  DELETE anything            - not reversible
  POST   /api/operators, /api/upload, site creation

Paths confirmed against a live BigFix 11 root server via /api/help.

Upstream candidates (see docs/besapi-proposals.md): the escaped dashboard
variable payload, and an import-from-string entry point - besapi's
`import_bes_to_site` only accepts a file path.
"""

import xml.sax.saxutils

from bigfix_root_mcp import content


def _check_status(result):
    """Raise requests.HTTPError on non-2xx; besapi only raises on 403."""
    result.request.raise_for_status()
    return result


def build_dashboard_variable_xml(
    dashboard_name: str, var_name: str, var_value: str, private: bool = False
) -> str:
    """Build the DashboardData payload with every field XML-escaped.

    besapi's `set_dashboard_variable_value` interpolates the dashboard name,
    variable name and value into this XML raw, so any of them containing
    "&" or "<" produces malformed XML. Same defect class as `get_target_xml`;
    this package builds its own payload for the same reason `clientquery.py`
    does.
    """
    escape = xml.sax.saxutils.escape
    return (
        '<BESAPI xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="BESAPI.xsd">'
        "<DashboardData>"
        f"<Dashboard>{escape(str(dashboard_name))}</Dashboard>"
        f"<Name>{escape(str(var_name))}</Name>"
        f"<IsPrivate>{str(bool(private)).lower()}</IsPrivate>"
        f"<Value>{escape(str(var_value))}</Value>"
        "</DashboardData>"
        "</BESAPI>"
    )


def dashboard_variable_path(dashboard_name: str, var_name: str) -> str:
    """REST path for a dashboard variable, each component validated."""
    return (
        f"dashboardvariable/{content.validate_path_segment(dashboard_name, 'dashboard_name')}"
        f"/{content.validate_path_segment(var_name, 'var_name')}"
    )


def set_dashboard_variable(
    conn, dashboard_name: str, var_name: str, var_value: str, private: bool = False
):
    """POST a dashboard datastore variable value."""
    path = dashboard_variable_path(dashboard_name, var_name)
    payload = build_dashboard_variable_xml(dashboard_name, var_name, var_value, private)
    return _check_status(conn.post(path, data=payload))


def stop_action_path(action_id) -> str:
    """REST path that stops an action, with the id coerced to int."""
    try:
        return f"action/{int(action_id)}/stop"
    except (TypeError, ValueError) as err:
        raise ValueError(f"action_id must be an integer, got {action_id!r}.") from err


def stop_action(conn, action_id):
    """POST /api/action/{id}/stop - stop an in-flight action."""
    return _check_status(conn.post(stop_action_path(action_id), data=""))


def import_path(site_path: str) -> str:
    """REST path for importing into a site."""
    return f"import/{content.validate_path_segment(site_path)}"


def import_bes_content(conn, site_path: str, bes_xml: str):
    """POST BES XML into a site, after validating it locally.

    Takes the XML as a string rather than a file path (besapi's
    `import_bes_to_site` is file-only, which is the wrong shape for a tool
    call), and refuses anything that does not validate against the BigFix
    schemas so a malformed document fails here rather than as a REST error.
    """
    path = import_path(site_path)  # validated before anything is built
    verdict = content.validate_bes_xml(bes_xml)
    if not verdict["valid"]:
        raise ValueError(f"bes_xml is not valid BES content: {verdict['reason']}")
    return _check_status(conn.post(path, data=bes_xml))
