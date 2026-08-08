"""Gated write surface.

The registration gate is the security boundary, so the tests that matter most
here are the ones about which tools exist, and about dry_run sending nothing.
"""

import importlib

import lxml.etree
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from bigfix_root_mcp import server, writes
from tests.conftest import FakeBESConnection, FakeRESTResult

WRITE_TOOLS = {"stop_action", "set_dashboard_variable", "import_bes_content"}


class TestDashboardVariableXml:
    """Besapi's set_dashboard_variable_value interpolates all three values into
    XML unescaped; this builder must not.
    """

    def test_hostile_value_round_trips(self):
        payload = writes.build_dashboard_variable_xml(
            "MyDash", "MyVar", 'a & b < c > d ]]> "quoted"'
        )
        root = lxml.etree.fromstring(payload.encode())
        value = root.find(".//Value").text
        assert value == 'a & b < c > d ]]> "quoted"'

    def test_hostile_name_round_trips(self):
        payload = writes.build_dashboard_variable_xml("D&D", "a<b", "v")
        root = lxml.etree.fromstring(payload.encode())
        assert root.find(".//Dashboard").text == "D&D"
        assert root.find(".//Name").text == "a<b"

    def test_private_flag_is_lowercased_xml_boolean(self):
        assert "<IsPrivate>true</IsPrivate>" in writes.build_dashboard_variable_xml(
            "d", "n", "v", private=True
        )
        assert "<IsPrivate>false</IsPrivate>" in writes.build_dashboard_variable_xml("d", "n", "v")

    def test_besapi_native_method_is_not_used(self):
        """Regression guard: routing through besapi would reintroduce the bug."""
        conn = FakeBESConnection()
        conn.post_responses.append(FakeRESTResult(text="<BESAPI/>"))
        writes.set_dashboard_variable(conn, "d", "n", "a & b")
        verb, path, data, _ = conn.calls[0]
        assert verb == "post"
        assert path == "dashboardvariable/d/n"
        assert "a &amp; b" in data


class TestStopAction:
    def test_posts_to_verified_stop_path(self):
        conn = FakeBESConnection()
        conn.post_responses.append(FakeRESTResult(text="<BESAPI/>"))
        writes.stop_action(conn, 900)
        assert conn.calls[0][1] == "action/900/stop"

    def test_action_id_must_be_an_integer(self):
        conn = FakeBESConnection()
        with pytest.raises(ValueError):
            writes.stop_action(conn, "900/../../admin")
        assert conn.calls == []


class TestImportBesContent:
    def test_invalid_xml_is_refused_before_any_post(self):
        conn = FakeBESConnection()
        with pytest.raises(ValueError, match="valid"):
            writes.import_bes_content(conn, "custom/MySite", "<notbes/>")
        assert conn.calls == []

    def test_traversal_in_site_path_is_refused_before_any_post(self):
        conn = FakeBESConnection()
        with pytest.raises(ValueError):
            writes.import_bes_content(conn, "custom/../../x", MINIMAL_BES)
        assert conn.calls == []

    def test_valid_content_posts_to_import_path(self):
        conn = FakeBESConnection()
        conn.post_responses.append(FakeRESTResult(text="<BESAPI/>"))
        writes.import_bes_content(conn, "custom/MySite", MINIMAL_BES)
        assert conn.calls[0][1] == "import/custom/MySite"


class TestWriteGate:
    async def test_write_tools_absent_by_default(self):
        """Paired with the gate-on test below: alone this would pass trivially."""
        async with Client(server.mcp) as client:
            tools = {tool.name for tool in await client.list_tools()}
        assert not (tools & WRITE_TOOLS)

    async def test_write_tools_present_when_gate_is_on(self, monkeypatch):
        monkeypatch.setenv("BIGFIX_ALLOW_WRITES", "true")
        reloaded = importlib.reload(server)
        try:
            async with Client(reloaded.mcp) as client:
                tools = {tool.name for tool in await client.list_tools()}
            assert WRITE_TOOLS <= tools
        finally:
            monkeypatch.delenv("BIGFIX_ALLOW_WRITES")
            importlib.reload(server)

    async def test_whoami_reports_the_gate_state(self, fake_conn):
        async with Client(server.mcp) as client:
            result = await client.call_tool("whoami", {})
        assert result.data["writes_enabled"] is False


class TestWriteToolBehavior:
    """Runs against a server module reloaded with the gate on."""

    @pytest.fixture
    def gated(self, monkeypatch):
        monkeypatch.setenv("BIGFIX_ALLOW_WRITES", "true")
        reloaded = importlib.reload(server)
        yield reloaded
        monkeypatch.delenv("BIGFIX_ALLOW_WRITES")
        importlib.reload(server)

    async def test_dry_run_is_the_default_and_sends_nothing(self, gated, fake_conn):
        async with Client(gated.mcp) as client:
            result = await client.call_tool("stop_action", {"action_id": 900})
        assert result.data["dry_run"] is True
        assert result.data["would_call"] == "POST action/900/stop"
        assert fake_conn.calls == []
        assert fake_conn.post_responses == []

    async def test_explicit_dry_run_false_actually_sends(self, gated, fake_conn):
        fake_conn.post_responses.append(FakeRESTResult(text="<BESAPI/>"))
        async with Client(gated.mcp) as client:
            result = await client.call_tool("stop_action", {"action_id": 900, "dry_run": False})
        assert result.data["dry_run"] is False
        assert fake_conn.calls[0][1] == "action/900/stop"

    async def test_dry_run_import_still_validates_the_xml(self, gated, fake_conn):
        async with Client(gated.mcp) as client:
            with pytest.raises(ToolError, match="valid"):
                await client.call_tool(
                    "import_bes_content",
                    {"site_path": "custom/MySite", "bes_xml": "<notbes/>"},
                )
        assert fake_conn.calls == []

    async def test_writes_are_audit_logged(self, gated, fake_conn, caplog):
        fake_conn.post_responses.append(FakeRESTResult(text="<BESAPI/>"))
        with caplog.at_level("INFO"):
            async with Client(gated.mcp) as client:
                await client.call_tool("stop_action", {"action_id": 900, "dry_run": False})
        audit = [r for r in caplog.records if "BIGFIX WRITE" in r.getMessage()]
        assert audit, "every executed write must leave an audit line"
        message = audit[0].getMessage()
        assert "stop_action" in message
        assert "testoperator" in message


# Title, Description and Relevance are the required Task elements per BES.xsd
MINIMAL_BES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<BES xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:noNamespaceSchemaLocation="BES.xsd">'
    "<Task>"
    "<Title>Test Task</Title>"
    "<Description>created by a test</Description>"
    "<Relevance>true</Relevance>"
    "</Task></BES>"
)
