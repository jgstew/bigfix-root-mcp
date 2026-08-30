"""Unit tests for the advisory static-analysis wrapper (analysis.py).

These run the real bigfix_relevance_analyzer: it is offline, deterministic
and stdlib-only, so faking it would only test the fake.
"""

import pytest

from bigfix_root_mcp import analysis


class TestPreflight:
    def test_clean_session_query_returns_none(self):
        assert analysis.preflight("number of bes computers", "session") is None

    def test_parse_error_is_a_finding(self):
        advisory = analysis.preflight("number of of bes computers", "session")
        assert advisory is not None
        codes = [f["code"] for f in advisory["findings"]]
        assert "parse-error" in codes
        # advisory payloads always say they are advisory
        assert "advisory" in advisory["note"].lower() or "snapshot" in advisory["note"].lower()

    def test_client_inspector_under_session_pinning_flags(self):
        advisory = analysis.preflight('exists file "c:\\x"', "session")
        assert advisory is not None
        # the inspector table's own evidence says this is client relevance
        assert "client" in advisory["dialect_mismatch"]
        assert "session" in advisory["dialect_mismatch"]

    def test_unknown_inspector_carries_suggestions(self):
        # one-letter-off from the real session inspector "bes computers"
        advisory = analysis.preflight("number of bes computerz", "session")
        assert advisory is not None
        unknown = [f for f in advisory["findings"] if f["code"] == "unknown-inspector"]
        assert unknown, "expected an unknown-inspector finding"
        assert any(f.get("suggestions") for f in unknown)

    def test_findings_capped(self, monkeypatch):
        # the analyzer coalesces same-rule findings, so force many findings to
        # prove the cap itself
        from bigfix_relevance_analyzer import Finding, Severity

        fakes = tuple(
            Finding(code="type-error", severity=Severity.ERROR, message=f"m{i}", path=None, line=1)
            for i in range(analysis.MAX_FINDINGS + 2)
        )
        monkeypatch.setattr(analysis, "lint_analysis", lambda *_a, **_k: fakes)
        advisory = analysis.preflight("number of bes computers", "session")
        assert advisory is not None
        assert len(advisory["findings"]) == analysis.MAX_FINDINGS
        assert advisory["findings_truncated"] == 2

    def test_analyzer_crash_never_breaks_a_call(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError("analyzer bug")

        monkeypatch.setattr(analysis, "analyze_relevance", boom)
        assert analysis.preflight("number of bes computers", "session") is None

    def test_bad_dialect_string_is_swallowed(self):
        # preflight is advisory: even a caller bug must not raise
        assert analysis.preflight("number of bes computers", "bogus") is None


class TestErrorAppendix:
    def test_none_renders_empty(self):
        assert analysis.error_appendix(None) == ""

    def test_findings_render_with_suggestions(self):
        advisory = analysis.preflight("number of bes computerz", "session")
        text = analysis.error_appendix(advisory)
        assert "unknown-inspector" in text
        assert "bes computers" in text  # a suggestion for the near-miss


class TestAnalyze:
    def test_parse_error_reported_in_band(self):
        report = analysis.analyze("number of of bes", None, None, False)
        assert report["parsed"] is False
        assert report["parse_error"]

    def test_clean_client_query(self):
        report = analysis.analyze('exists file "c:\\x"', "client", None, False)
        assert report["parsed"] is True
        assert report["parse_error"] is None
        assert report["dialect"]["effective"] == "client"
        assert "sexpr" not in report

    def test_sexpr_only_on_request(self):
        report = analysis.analyze("number of bes computers", "session", None, True)
        assert report["sexpr"]

    def test_bad_dialect_raises_value_error(self):
        with pytest.raises(ValueError, match="dialect"):
            analysis.analyze("true", "bogus", None, False)


class TestSearch:
    def test_finds_session_inspector(self):
        result = analysis.search("bes computers", None, None, 10)
        assert result["matches"]
        assert any("bes computer" in m["name"] for m in result["matches"])

    def test_limit_respected(self):
        result = analysis.search("file", None, None, 3)
        assert len(result["matches"]) <= 3

    def test_bad_dialect_raises_value_error(self):
        with pytest.raises(ValueError, match="dialect"):
            analysis.search("file", "bogus", None, 5)

    def test_bad_kind_raises_value_error(self):
        with pytest.raises(ValueError, match="kind"):
            analysis.search("file", None, "bogus", 5)
