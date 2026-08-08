"""Relevance error hinting.

Error strings here are real, captured from a live BigFix 11 root server while
probing inspectors (see docs/rest-endpoints.md).
"""

import pytest
from fastmcp.exceptions import ToolError

from bigfix_root_mcp.errors import check_relevance_envelope


def message_for(error: str) -> str:
    with pytest.raises(ToolError) as caught:
        check_relevance_envelope({"error": error})
    return str(caught.value)


class TestRelevanceHints:
    def test_original_error_is_always_preserved(self):
        assert "not defined" in message_for('The operator "firsts" is not defined.')

    @pytest.mark.parametrize("operator", ["first", "firsts", "items", "elements"])
    def test_row_limiting_operators_get_the_limit_hint(self, operator):
        message = message_for(f'The operator "{operator}" is not defined.')
        assert "limit" in message.lower()

    def test_unknown_operator_gets_a_generic_inspector_hint(self):
        message = message_for('The operator "gather url" is not defined.')
        assert "inspector" in message.lower()

    def test_singular_plural_error_gets_its_own_hint(self):
        message = message_for("Singular expression refers to nonexistent object.")
        assert "plural" in message.lower()

    def test_client_relevance_in_session_query_is_called_out(self):
        # `exists file` is client relevance; a common model mistake
        message = message_for('The operator "file" is not defined.')
        assert "client" in message.lower()

    def test_unhinted_error_still_raises_cleanly(self):
        message = message_for("A completely novel parse failure.")
        assert "novel parse failure" in message

    def test_healthy_envelope_passes_through(self):
        envelope = {"result": [1], "evaltime_ms": 2}
        assert check_relevance_envelope(envelope) is envelope
