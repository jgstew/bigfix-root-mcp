"""BigFix action read helpers built on besapi.

besapi has no action support at all, so this is new code written in the
upstreamable shape (`conn` first, no fastmcp imports, besapi-style
exceptions) - see docs/besapi-proposals.md.

Paths confirmed against a live BigFix 11 root server via /api/help/action:

    GET    /api/action/{id}
    GET    /api/action/{id}/status
    DELETE /api/action/{id}
    POST   /api/action/{id}/stop
    GET    /api/actions
    POST   /api/actions

Only the GETs live here. `POST /api/action/{id}/stop` is a write and lives in
writes.py behind the write gate; `POST /api/actions` (deploy) and the DELETE
are deliberately not implemented anywhere in this package.
"""


def _action_id(value) -> int:
    """Coerce an action id to int so it can never carry path syntax."""
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"action_id must be an integer, got {value!r}.") from err


def get_action(conn, action_id):
    """GET /api/action/{id} - the action definition."""
    return conn.get(f"action/{_action_id(action_id)}")


def get_action_status(conn, action_id):
    """GET /api/action/{id}/status - per-computer execution state."""
    return conn.get(f"action/{_action_id(action_id)}/status")


def list_actions(conn):
    """GET /api/actions - all actions visible to the configured operator."""
    return conn.get("actions")
