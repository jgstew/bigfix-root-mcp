"""End-to-end tool tests via an in-memory fastmcp Client (no network)."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from bigfix_root_mcp import response, server
from tests.conftest import FakeRESTResult
from tests.test_clientquery import SUBMIT_RESPONSE_XML, make_results_envelope
from tests.test_content import SITES_XML


async def call(tool, arguments=None):
    async with Client(server.mcp) as client:
        return await client.call_tool(tool, arguments or {})


class TestSessionRelevance:
    async def test_happy_path(self, fake_conn):
        fake_conn.relevance_responses.append(
            {"result": ["computer1", "computer2"], "evaltime_ms": 12}
        )
        result = await call("session_relevance_query", {"relevance": "names of bes computers"})
        assert result.data["result"] == ["computer1", "computer2"]
        assert result.data["evaltime_ms"] == 12
        assert fake_conn.calls[0][:2] == (
            "session_relevance_json",
            "names of bes computers",
        )

    async def test_relevance_error_envelope(self, fake_conn):
        fake_conn.relevance_responses.append(
            {"error": "Singular expression refers to nonexistent object."}
        )
        with pytest.raises(ToolError, match="Session relevance error"):
            await call("session_relevance_query", {"relevance": "bogus of nothing"})

    async def test_permission_error_mentions_permissions(self, fake_conn):
        fake_conn.relevance_responses.append(
            PermissionError("HTTP Response Status Code: `403` Forbidden")
        )
        with pytest.raises(ToolError, match="permission"):
            await call("session_relevance_query", {"relevance": "names of bes users"})


class TestClientQueryTools:
    async def test_submit_returns_id_and_expected_count(self, fake_conn):
        fake_conn.post_responses.append(FakeRESTResult(text=SUBMIT_RESPONSE_XML))
        result = await call(
            "client_query_submit",
            {"query_text": "computer name", "target_computer_ids": [10, 20]},
        )
        assert result.data == {"query_id": 42, "expected_count": 2}

    async def test_submit_requires_exactly_one_target(self, fake_conn):
        with pytest.raises(ToolError, match="Exactly one targeting mode"):
            await call("client_query_submit", {"query_text": "computer name"})

    async def test_results_summarized(self, fake_conn):
        rows = [{"computerID": 1, "result": "a"}, {"computerID": 2, "result": "b"}]
        fake_conn.get_responses.append(FakeRESTResult(text=json.dumps(make_results_envelope(rows))))
        result = await call("client_query_results", {"query_id": 42})
        assert result.data["reported_count"] == 2
        assert result.data["results"] == rows

    async def test_full_client_query_flow(self, fake_conn):
        fake_conn.post_responses.append(FakeRESTResult(text=SUBMIT_RESPONSE_XML))
        fake_conn.get_responses.extend(
            [
                FakeRESTResult(
                    text=json.dumps(make_results_envelope([{"computerID": 10, "result": "a"}]))
                ),
                FakeRESTResult(
                    text=json.dumps(
                        make_results_envelope(
                            [
                                {"computerID": 10, "result": "a"},
                                {"computerID": 20, "result": "b"},
                            ]
                        )
                    )
                ),
            ]
        )
        result = await call(
            "client_query",
            {
                "query_text": "computer name",
                "target_computer_ids": [10, 20],
                "timeout_seconds": 30,
                "poll_interval_seconds": 2,
            },
        )
        assert result.data["stop_reason"] == "expected_count_reached"
        assert result.data["reported_count"] == 2
        assert result.data["query_id"] == 42


class TestOtherTools:
    async def test_whoami(self, fake_conn):
        result = await call("whoami")
        assert result.data["username"] == "testoperator"
        assert result.data["rootserver"] == "https://bes.example.com:52311"
        assert result.data["is_main_operator"] is True

    async def test_get_server_info(self, fake_conn):
        fake_conn.get_responses.append(
            FakeRESTResult(
                text=json.dumps({"serverVersion": "11.0.3"}),
                headers={"content-type": "application/json"},
            )
        )
        result = await call("get_server_info")
        assert result.data["serverVersion"] == "11.0.3"

    async def test_get_computer_group_passes_explicit_site_path(self, fake_conn):
        fake_conn.get_responses.append(
            FakeRESTResult(
                text=(
                    "<BESAPI>"
                    '<ComputerGroup Resource="https://bes.example.com:52311/api/'
                    'computergroup/custom/MySite/55">'
                    "<Name>My Group</Name><ID>55</ID></ComputerGroup>"
                    "</BESAPI>"
                )
            )
        )
        result = await call(
            "get_computer_group",
            {"group_name": "My Group", "site_path": "custom/MySite"},
        )
        assert result.data["resource"].endswith("/custom/MySite/55")
        # the explicit site path must be in the request path (no fallback to
        # any connection-level "current site path" state)
        method, path, _ = fake_conn.calls[0]
        assert method == "get"
        assert path == "computergroups/custom/MySite"

    async def test_get_computer_group_not_found(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI></BESAPI>"))
        with pytest.raises(ToolError, match="not found in site 'master'"):
            await call(
                "get_computer_group",
                {"group_name": "Missing", "site_path": "master"},
            )

    async def test_get_operator_not_found(self, fake_conn):
        with pytest.raises(ToolError, match="not found"):
            await call("get_operator", {"user_name": "ghost"})

    async def test_get_dashboard_variable(self, fake_conn):
        result = await call(
            "get_dashboard_variable",
            {"dashboard_name": "MyDash", "var_name": "MyVar"},
        )
        assert result.data == {
            "dashboard": "MyDash",
            "name": "MyVar",
            "value": "fake-value",
        }

    async def test_api_get(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI>help text</BESAPI>"))
        result = await call("api_get", {"path": "/api/help"})
        assert result.data["status_code"] == 200
        assert "help text" in result.data["text"]
        # leading /api/ is stripped so besapi's url() doesn't double it
        assert fake_conn.calls[0][1] == "help"

    async def test_api_get_rejects_absolute_url(self, fake_conn):
        with pytest.raises(ToolError, match="relative"):
            await call("api_get", {"path": "https://evil.example.com/steal"})

    async def test_api_get_http_error(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="Not Found", status_code=404))
        with pytest.raises(ToolError, match="404"):
            await call("api_get", {"path": "nonexistent"})


class TestResponseShaping:
    """No tool may return an unbounded payload without saying it was cut."""

    async def test_session_relevance_bounds_large_result(self, fake_conn):
        fake_conn.relevance_responses.append(
            {"result": [f"computer{i}" for i in range(1000)], "evaltime_ms": 900}
        )
        result = await call("session_relevance_query", {"relevance": "bes computers"})
        assert len(result.data["result"]) == response.DEFAULT_LIMIT
        assert result.data["truncated"] is True
        assert result.data["total_available"] == 1000
        # metadata from the envelope must survive bounding
        assert result.data["evaltime_ms"] == 900

    async def test_session_relevance_honors_limit_and_offset(self, fake_conn):
        fake_conn.relevance_responses.append({"result": list(range(10)), "evaltime_ms": 1})
        result = await call(
            "session_relevance_query",
            {"relevance": "bes computers", "limit": 3, "offset": 5},
        )
        assert result.data["result"] == [5, 6, 7]
        assert result.data["offset"] == 5
        assert result.data["truncated"] is True

    async def test_session_relevance_small_result_not_flagged(self, fake_conn):
        fake_conn.relevance_responses.append({"result": ["a"], "evaltime_ms": 1})
        result = await call("session_relevance_query", {"relevance": "x"})
        assert result.data["truncated"] is False
        assert result.data["total_available"] == 1

    async def test_client_query_results_bounds_rows(self, fake_conn):
        rows = [{"computerID": i, "result": "x"} for i in range(1000)]
        fake_conn.get_responses.append(FakeRESTResult(text=json.dumps(make_results_envelope(rows))))
        result = await call("client_query_results", {"query_id": 42, "limit": 10})
        assert len(result.data["results"]) == 10
        assert result.data["truncated"] is True
        # counts describe the whole query, not the returned window
        assert result.data["reported_count"] == 1000
        assert result.data["result_row_count"] == 1000

    async def test_api_get_reports_total_chars(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="y" * 100))
        result = await call("api_get", {"path": "help", "max_chars": 10})
        assert result.data["text"] == "y" * 10
        assert result.data["truncated"] is True
        assert result.data["total_chars"] == 100

    async def test_list_sites_has_stable_bounded_shape(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        result = await call("list_sites")
        assert result.data["truncated"] is False
        assert result.data["data"] is not None


class TestReadCoverageTools:
    """Paths asserted here were confirmed live via /api/help."""

    async def test_get_computer(self, fake_conn):
        fake_conn.get_responses.append(
            FakeRESTResult(text="<BESAPI><Computer><ID>7</ID></Computer></BESAPI>")
        )
        result = await call("get_computer", {"computer_id": 7})
        assert result.data["truncated"] is False
        assert fake_conn.calls[0][1] == "computer/7"

    async def test_applicable_fixlets(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call("applicable_fixlets", {"computer_id": 7})
        assert fake_conn.calls[0][1] == "computer/7/fixlets"

    async def test_get_action_status(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call("get_action_status", {"action_id": 900})
        assert fake_conn.calls[0][1] == "action/900/status"

    async def test_get_action(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call("get_action", {"action_id": 900})
        assert fake_conn.calls[0][1] == "action/900"

    async def test_list_actions(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call("list_actions")
        assert fake_conn.calls[0][1] == "actions"

    async def test_get_content_by_kind(self, fake_conn):
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call(
            "get_content",
            {"kind": "task", "site_path": "custom/MySite", "content_id": 12},
        )
        assert fake_conn.calls[0][1] == "task/custom/MySite/12"

    async def test_get_content_rejects_traversal(self, fake_conn):
        with pytest.raises(ToolError, match=r"\.\."):
            await call(
                "get_content",
                {"kind": "task", "site_path": "master/../../rd", "content_id": 12},
            )
        assert fake_conn.calls == []

    async def test_find_computers_is_bounded(self, fake_conn):
        fake_conn.relevance_responses.append(
            {"result": [[f"host{i}", i] for i in range(200)], "evaltime_ms": 5}
        )
        result = await call("find_computers", {"name_contains": "host", "limit": 10})
        assert len(result.data["result"]) == 10
        assert result.data["total_available"] == 200
        assert 'contains "host"' in fake_conn.calls[0][1]

    async def test_find_content_uses_kind_inspector(self, fake_conn):
        fake_conn.relevance_responses.append({"result": [], "evaltime_ms": 1})
        fake_conn.get_responses.append(FakeRESTResult(text="<BESAPI/>"))
        await call("find_content", {"kind": "analysis", "name_contains": "cpu"})
        assert "bes analyses" in fake_conn.calls[0][1]

    async def test_find_content_resolves_site_path_for_get_content(self, fake_conn):
        """The find -> get handoff: relevance gives a site name, not a path."""
        fake_conn.relevance_responses.append(
            {"result": [["Fix A", 1, "BES Support"]], "evaltime_ms": 1}
        )
        fake_conn.get_responses.append(FakeRESTResult(text=SITES_XML))
        result = await call("find_content", {"kind": "fixlet", "name_contains": "fix"})
        row = result.data["result"][0]
        assert row["site_path"] == "external/BES%20Support"
        assert row["id"] == 1

    async def test_find_content_rejects_quote_in_term(self, fake_conn):
        with pytest.raises(ToolError, match="double quote"):
            await call("find_content", {"kind": "fixlet", "name_contains": 'a" of x'})

    async def test_list_operators_and_roles(self, fake_conn):
        fake_conn.get_responses.extend(
            [FakeRESTResult(text="<BESAPI/>"), FakeRESTResult(text="<BESAPI/>")]
        )
        await call("list_operators")
        await call("list_roles")
        assert fake_conn.calls[0][1] == "operators"
        assert fake_conn.calls[1][1] == "roles"

    async def test_validate_bes_xml_makes_no_server_call(self, fake_conn):
        result = await call("validate_bes_xml", {"bes_xml": "<notbes/>"})
        assert result.data["valid"] is False
        assert fake_conn.calls == []

    async def test_get_computer_group_rejects_traversal(self, fake_conn):
        """Regression: the old code passed 'master/../../x' straight through."""
        with pytest.raises(ToolError, match=r"\.\."):
            await call(
                "get_computer_group",
                {"group_name": "g", "site_path": "master/../../rd"},
            )
        assert fake_conn.calls == []


class TestToolSurface:
    async def test_only_expected_tools_registered(self):
        """Read-only guard: the registered tool list IS the security surface."""
        async with Client(server.mcp) as client:
            tools = {tool.name for tool in await client.list_tools()}
        assert tools == {
            "session_relevance_query",
            "client_query_submit",
            "client_query_results",
            "client_query",
            "get_server_info",
            "list_sites",
            "get_computer_group",
            "get_operator",
            "get_dashboard_variable",
            "whoami",
            "api_get",
            "get_computer",
            "find_computers",
            "applicable_fixlets",
            "get_action",
            "get_action_status",
            "list_actions",
            "find_content",
            "get_content",
            "list_operators",
            "list_roles",
            "validate_bes_xml",
        }
