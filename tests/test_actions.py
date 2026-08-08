"""Action read helpers.

Paths confirmed live via /api/help/action:
  GET  /api/action/{id}
  GET  /api/action/{id}/status
  GET  /api/actions
besapi has no action support, so all of this is new code here.
"""

import pytest

from bigfix_root_mcp import actions
from tests.conftest import FakeBESConnection, FakeRESTResult


@pytest.fixture
def conn():
    connection = FakeBESConnection()
    connection.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
    return connection


class TestActionReads:
    def test_get_action_path(self, conn):
        actions.get_action(conn, 900)
        assert conn.calls[0][1] == "action/900"

    def test_get_action_status_path(self, conn):
        actions.get_action_status(conn, 900)
        assert conn.calls[0][1] == "action/900/status"

    def test_list_actions_path(self, conn):
        actions.list_actions(conn)
        assert conn.calls[0][1] == "actions"

    def test_action_id_is_coerced_to_int(self, conn):
        # blocks path injection through a string action id
        actions.get_action(conn, "900")
        assert conn.calls[0][1] == "action/900"

    def test_non_numeric_action_id_is_rejected(self, conn):
        with pytest.raises(ValueError):
            actions.get_action(conn, "900/../../admin")
        assert conn.calls == []
