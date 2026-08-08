"""Bounding helpers: every tool response must be size-bounded and say so."""

import pytest

from bigfix_root_mcp import response


class TestBoundList:
    def test_empty_list_is_not_truncated(self):
        out = response.bound_list([])
        assert out == {
            "items": [],
            "returned": 0,
            "offset": 0,
            "total_available": 0,
            "truncated": False,
        }

    def test_under_limit_returns_everything(self):
        out = response.bound_list([1, 2, 3], limit=10)
        assert out["items"] == [1, 2, 3]
        assert out["returned"] == 3
        assert out["total_available"] == 3
        assert out["truncated"] is False

    def test_exact_boundary_is_not_truncated(self):
        out = response.bound_list([1, 2, 3], limit=3)
        assert out["items"] == [1, 2, 3]
        assert out["truncated"] is False

    def test_over_limit_truncates_and_reports_total(self):
        out = response.bound_list(list(range(100)), limit=10)
        assert out["items"] == list(range(10))
        assert out["returned"] == 10
        assert out["total_available"] == 100
        assert out["truncated"] is True

    def test_offset_windows_into_the_list(self):
        out = response.bound_list(list(range(10)), limit=3, offset=5)
        assert out["items"] == [5, 6, 7]
        assert out["offset"] == 5
        assert out["total_available"] == 10
        assert out["truncated"] is True

    def test_offset_past_end_returns_empty_but_stays_truthful(self):
        # nothing came back, yet 10 rows exist - the caller must not read this
        # as "there is no data"
        out = response.bound_list(list(range(10)), limit=5, offset=99)
        assert out["items"] == []
        assert out["returned"] == 0
        assert out["total_available"] == 10
        assert out["truncated"] is True

    def test_default_limit_applies_when_unspecified(self):
        out = response.bound_list(list(range(response.DEFAULT_LIMIT + 50)))
        assert out["returned"] == response.DEFAULT_LIMIT
        assert out["truncated"] is True

    @pytest.mark.parametrize("bad_limit", [0, -1])
    def test_limit_below_one_is_rejected(self, bad_limit):
        with pytest.raises(ValueError, match="limit"):
            response.bound_list([1, 2, 3], limit=bad_limit)

    def test_negative_offset_is_rejected(self):
        with pytest.raises(ValueError, match="offset"):
            response.bound_list([1, 2, 3], offset=-1)


class TestBoundMapping:
    """For besdict-shaped blobs whose internal structure we don't want to guess."""

    def test_small_payload_passes_through(self):
        out = response.bound_mapping({"a": 1}, max_chars=1000)
        assert out["data"] == {"a": 1}
        assert out["truncated"] is False
        assert out["note"] == ""

    def test_oversized_payload_is_dropped_not_mangled(self):
        payload = {"rows": ["x" * 100 for _ in range(100)]}
        out = response.bound_mapping(payload, max_chars=200)
        assert out["data"] is None
        assert out["truncated"] is True
        assert out["note"]  # must tell the caller what to do instead
        assert out["total_chars"] > 200

    def test_keys_are_stable_across_both_outcomes(self):
        small = response.bound_mapping({"a": 1}, max_chars=1000)
        big = response.bound_mapping({"a": "x" * 5000}, max_chars=100)
        assert small.keys() == big.keys()

    def test_non_serializable_values_do_not_raise(self):
        out = response.bound_mapping({"when": object()}, max_chars=1000)
        assert out["truncated"] is False


class TestBoundText:
    def test_short_text_passes_through(self):
        out = response.bound_text("hello", max_chars=100)
        assert out == {"text": "hello", "truncated": False, "total_chars": 5}

    def test_exact_boundary_is_not_truncated(self):
        out = response.bound_text("abcde", max_chars=5)
        assert out["text"] == "abcde"
        assert out["truncated"] is False

    def test_long_text_is_cut_and_flagged(self):
        out = response.bound_text("x" * 1000, max_chars=100)
        assert out["text"] == "x" * 100
        assert out["truncated"] is True
        assert out["total_chars"] == 1000

    def test_default_max_chars_applies(self):
        out = response.bound_text("x" * (response.MAX_RESPONSE_CHARS + 1))
        assert len(out["text"]) == response.MAX_RESPONSE_CHARS
        assert out["truncated"] is True
