"""Uniform response bounding.

Every tool that can return an unbounded amount of data routes its payload
through here, so a large fleet produces a truncated answer that *says* it is
truncated rather than megabytes of JSON in the client's context window.

Deliberately not an upstream candidate: this is an MCP context-window concern,
not BigFix REST logic. Equally deliberate: nothing here rewrites the caller's
query to bound it server-side (e.g. injecting `firsts N` into relevance).
Silently answering a different question than the one asked is worse than
returning a big answer and admitting it was cut.
"""

import json

# rows returned by a list tool when the caller does not specify a limit
DEFAULT_LIMIT = 500
# characters of raw text returned by a text tool (api_get and friends)
MAX_RESPONSE_CHARS = 50_000


def bound_list(rows: list, limit: int | None = None, offset: int = 0) -> dict:
    """Window a list of rows, reporting what was left out.

    `truncated` means "you did not receive everything that exists" - which
    includes the case of an offset past the end of the list, where zero rows
    come back but rows do exist. A caller must never read an empty `items` as
    "there is no data".
    """
    if limit is None:
        limit = DEFAULT_LIMIT
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}.")
    if offset < 0:
        raise ValueError(f"offset must not be negative, got {offset}.")

    total = len(rows)
    items = rows[offset : offset + limit]
    return {
        "items": items,
        "returned": len(items),
        "offset": offset,
        "total_available": total,
        "truncated": len(items) < total,
    }


def bound_mapping(payload, max_chars: int | None = None, hint: str = "") -> dict:
    """Pass a structured payload through unless its JSON form is oversized.

    For besapi `besdict` blobs, whose nesting varies by endpoint and BigFix
    version. Windowing them would mean guessing at their internal shape, so an
    oversized payload is dropped entirely rather than cut at an arbitrary point
    into something that looks complete but isn't.

    The key set is identical in both outcomes - only `data` goes null - so a
    caller never has to branch on which shape it got.
    """
    if max_chars is None:
        max_chars = MAX_RESPONSE_CHARS

    encoded = json.dumps(payload, default=str)
    oversized = len(encoded) > max_chars
    note = ""
    if oversized:
        note = (
            f"Response omitted: {len(encoded)} characters exceeds the "
            f"{max_chars} limit. Narrow the request, or use "
            "session_relevance_query to select only the fields you need."
        )
        if hint:
            note = f"{note} {hint}"
    return {
        "data": None if oversized else payload,
        "truncated": oversized,
        "total_chars": len(encoded),
        "note": note,
    }


def bound_text(text: str, max_chars: int | None = None) -> dict:
    """Cap a raw text payload, reporting the original length."""
    if max_chars is None:
        max_chars = MAX_RESPONSE_CHARS
    if max_chars < 1:
        raise ValueError(f"max_chars must be at least 1, got {max_chars}.")

    text = str(text)
    return {
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "total_chars": len(text),
    }
