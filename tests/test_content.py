"""Content/computer read helpers and REST path safety.

Endpoint paths asserted here were confirmed against a live BigFix 11 root
server via /api/help (see docs/rest-endpoints.md), not inferred.
"""

import pytest

from bigfix_root_mcp import content
from tests.conftest import FakeRESTResult


class TestValidatePathSegment:
    """Closes security-review finding 3: a `..` prefix check is not enough."""

    @pytest.mark.parametrize("good", ["master", "custom/MySite", "operator/Some Operator"])
    def test_legitimate_site_paths_survive(self, good):
        assert content.validate_path_segment(good) == good.replace(" ", "%20")

    @pytest.mark.parametrize(
        "bad",
        [
            "master/../../rd",
            "../secrets",
            "custom/./MySite",
            "..",
            ".",
            "custom//MySite",
            "",
            "   ",
        ],
    )
    def test_traversal_and_empty_segments_are_rejected(self, bad):
        with pytest.raises(ValueError):
            content.validate_path_segment(bad)

    def test_query_string_is_encoded_not_passed_through(self):
        # a `?` must not be able to graft a query onto the request
        assert "?" not in content.validate_path_segment("custom/My?Site")

    @pytest.mark.parametrize("encoded", ["custom/%2e%2e", "%2E%2E/x", "custom/%2e"])
    def test_percent_encoded_traversal_is_rejected(self, encoded):
        """Validation must happen after decoding, or %2e%2e walks straight past."""
        with pytest.raises(ValueError):
            content.validate_path_segment(encoded)

    def test_site_name_containing_a_slash_stays_one_segment(self):
        """Real deployments have sites like custom/Public%2fWindows."""
        assert content.validate_path_segment("custom/Public%2fWindows") == "custom/Public%2FWindows"

    @pytest.mark.parametrize(
        "path",
        ["master", "custom/Public%2fWindows", "operator/Some Operator", "custom/A B"],
    )
    def test_encoding_is_idempotent(self, path):
        once = content.validate_path_segment(path)
        assert content.validate_path_segment(once) == once


class TestRelevanceBuilders:
    def test_computer_search_is_case_insensitive_substring(self):
        rel = content.build_computer_search_relevance("web")
        assert 'contains "web"' in rel
        assert "bes computers" in rel

    def test_content_search_uses_the_right_inspector_per_kind(self):
        assert "bes tasks" in content.build_content_search_relevance("task", "patch")
        assert "bes analyses" in content.build_content_search_relevance("analysis", "patch")
        assert "bes baselines" in content.build_content_search_relevance("baseline", "patch")
        assert "bes fixlets" in content.build_content_search_relevance("fixlet", "patch")

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="kind"):
            content.build_content_search_relevance("wizard", "patch")

    def test_search_term_is_lowercased_to_match_the_comparison(self):
        rel = content.build_content_search_relevance("fixlet", "PATCH")
        assert 'contains "patch"' in rel

    @pytest.mark.parametrize("hostile", ['a" of bes computers; "', 'x"y'])
    def test_quote_in_search_term_is_rejected(self, hostile):
        # relevance has no portable string-literal escape, so refuse rather
        # than build an expression that means something else
        with pytest.raises(ValueError, match="double quote"):
            content.build_computer_search_relevance(hostile)


class TestRestReads:
    def test_get_computer_uses_verified_path(self):
        conn = _conn_with("<BESAPI><Computer><ID>7</ID></Computer></BESAPI>")
        content.get_computer(conn, 7)
        assert conn.calls[0][1] == "computer/7"

    def test_get_computer_coerces_id_to_int(self):
        conn = _conn_with("<BESAPI/>")
        content.get_computer(conn, "7")
        assert conn.calls[0][1] == "computer/7"

    def test_get_computer_fixlets_uses_subresource(self):
        conn = _conn_with("<BESAPI/>")
        content.get_computer_fixlets(conn, 7)
        assert conn.calls[0][1] == "computer/7/fixlets"

    @pytest.mark.parametrize(
        "kind,expected",
        [
            ("fixlet", "fixlet/custom/MySite/12"),
            ("task", "task/custom/MySite/12"),
            ("analysis", "analysis/custom/MySite/12"),
            ("baseline", "baseline/custom/MySite/12"),
        ],
    )
    def test_get_content_path_per_kind(self, kind, expected):
        conn = _conn_with("<BESAPI/>")
        content.get_content(conn, kind, "custom/MySite", 12)
        assert conn.calls[0][1] == expected

    def test_get_content_rejects_traversal_in_site_path(self):
        conn = _conn_with("<BESAPI/>")
        with pytest.raises(ValueError):
            content.get_content(conn, "fixlet", "master/../../rd", 12)
        assert conn.calls == []  # nothing was sent

    def test_get_content_rejects_unknown_kind(self):
        conn = _conn_with("<BESAPI/>")
        with pytest.raises(ValueError, match="kind"):
            content.get_content(conn, "wizard", "master", 12)


SITES_XML = """<BESAPI>
  <ExternalSite Resource="https://h:52311/api/site/external/BES%20Support">
    <Name>BES Support</Name></ExternalSite>
  <CustomSite Resource="https://h:52311/api/site/custom/Public%2fWindows">
    <Name>Public/Windows</Name></CustomSite>
  <ActionSite Resource="https://h:52311/api/site/master">
    <Name>ActionSite</Name></ActionSite>
  <OperatorSite Resource="https://h:52311/api/site/operator/Bob">
    <Name>Bob</Name></OperatorSite>
</BESAPI>"""


class TestSitePathMap:
    """Session relevance exposes a site NAME but no site PATH inspector, so the
    path has to come from the REST Resource URL.
    """

    def test_maps_each_site_type_to_its_real_path(self):
        conn = _conn_with(SITES_XML)
        mapping = content.build_site_path_map(conn)
        assert mapping["BES Support"] == ["external/BES%20Support"]
        assert mapping["ActionSite"] == ["master"]
        assert mapping["Bob"] == ["operator/Bob"]

    def test_site_name_containing_slash_keeps_its_encoding(self):
        conn = _conn_with(SITES_XML)
        mapping = content.build_site_path_map(conn)
        assert mapping["Public/Windows"] == ["custom/Public%2fWindows"]

    def test_duplicate_names_across_types_are_all_kept(self):
        xml = """<BESAPI>
          <ExternalSite Resource="https://h:52311/api/site/external/Dup">
            <Name>Dup</Name></ExternalSite>
          <CustomSite Resource="https://h:52311/api/site/custom/Dup">
            <Name>Dup</Name></CustomSite>
        </BESAPI>"""
        mapping = content.build_site_path_map(_conn_with(xml))
        assert sorted(mapping["Dup"]) == ["custom/Dup", "external/Dup"]

    def test_resolved_path_round_trips_through_validation(self):
        mapping = content.build_site_path_map(_conn_with(SITES_XML))
        for paths in mapping.values():
            for path in paths:
                content.validate_path_segment(path)  # must not raise


class TestAnnotateSitePaths:
    def test_rows_become_dicts_with_resolved_paths(self):
        rows = [["Fix A", 1, "BES Support"], ["Fix B", 2, "ActionSite"]]
        out = content.annotate_content_rows(
            rows,
            {
                "BES Support": ["external/BES%20Support"],
                "ActionSite": ["master"],
            },
        )
        assert out[0] == {
            "name": "Fix A",
            "id": 1,
            "site_name": "BES Support",
            "site_path": "external/BES%20Support",
        }
        assert out[1]["site_path"] == "master"

    def test_ambiguous_site_name_yields_null_path(self):
        rows = [["Fix", 1, "Dup"]]
        out = content.annotate_content_rows(rows, {"Dup": ["custom/Dup", "external/Dup"]})
        assert out[0]["site_path"] is None
        assert sorted(out[0]["site_path_candidates"]) == ["custom/Dup", "external/Dup"]

    def test_unknown_site_name_yields_null_path(self):
        out = content.annotate_content_rows([["Fix", 1, "Ghost"]], {})
        assert out[0]["site_path"] is None

    def test_malformed_row_is_passed_through_untouched(self):
        assert content.annotate_content_rows([["only-two", 1]], {}) == [["only-two", 1]]


class TestComputerGroupLookup:
    def test_http_error_is_raised_not_parsed(self):
        """A 404 must not fall through into XML parsing as 'not found'."""
        import requests

        conn = _conn_with("Not Found")
        conn.get_responses[0].request.status_code = 404
        with pytest.raises(requests.exceptions.HTTPError):
            content.get_computer_group_by_name(conn, "g", "master")

    def test_missing_group_returns_none(self):
        conn = _conn_with("<BESAPI></BESAPI>")
        assert content.get_computer_group_by_name(conn, "ghost", "master") is None


class TestValidateBesXml:
    def test_wellformed_but_not_bes_is_invalid(self):
        out = content.validate_bes_xml("<notbes><x/></notbes>")
        assert out["valid"] is False
        assert out["reason"]

    def test_malformed_xml_is_reported_not_raised(self):
        out = content.validate_bes_xml("<BES><unclosed>")
        assert out["valid"] is False
        assert "well-formed" in out["reason"]

    def test_entity_expansion_is_not_performed(self):
        # billion-laughs shape: must be refused/unexpanded, never blow up memory
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]><BES>&lol2;</BES>"
        )
        out = content.validate_bes_xml(bomb)
        assert out["valid"] is False

    def test_accepts_bytes_as_well_as_str(self):
        assert content.validate_bes_xml(b"<notbes/>")["valid"] is False


def _conn_with(text):
    from tests.conftest import FakeBESConnection

    conn = FakeBESConnection()
    conn.get_responses.append(FakeRESTResult(text=text))
    return conn
