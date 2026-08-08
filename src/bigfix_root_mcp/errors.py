"""Map besapi/requests failures to MCP ToolErrors.

besapi does not raise on HTTP errors except 403 (PermissionError inside
RESTResult) and login failures (requests.HTTPError), so every tool must
check status codes explicitly via check_rest_result().
"""

import functools
import inspect
import json

import requests
from fastmcp.exceptions import ToolError

# cap error/response snippets so tool errors stay readable
_SNIPPET_LEN = 500


def _snippet(text: str) -> str:
    text = str(text).strip()
    if len(text) > _SNIPPET_LEN:
        return text[:_SNIPPET_LEN] + "..."
    return text


def check_rest_result(result, context: str):
    """Raise ToolError if a besapi RESTResult has a non-2xx status code.

    Returns the result unchanged otherwise.
    """
    status = result.request.status_code
    if not 200 <= status < 300:
        raise ToolError(
            f"{context}: BigFix REST API returned HTTP {status}: " f"{_snippet(result.text)}"
        )
    return result


# operators a model reaches for to bound a result set, none of which exist on
# a BigFix root server (verified live - see docs/rest-endpoints.md)
_LIMIT_OPERATORS = ("first", "firsts", "items", "elements")

# inspectors that only exist in *client* relevance; asking for them in a
# session relevance query is the single most common mistake
_CLIENT_ONLY_INSPECTORS = (
    "file",
    "folder",
    "registry",
    "regapp",
    "process",
    "running application",
    "operating system",
)


def _relevance_hint(error: str) -> str:
    """Suggest a cause for a known relevance error shape.

    A bare BigFix error is a dead end for a model - it says what failed but
    never what to do instead. These hints turn the common failures into a
    retry. Returns "" when nothing useful can be said.
    """
    lowered = error.lower()

    if "is not defined" in lowered:
        # pull the quoted operator name out of: The operator "x" is not defined.
        quoted = ""
        if '"' in error:
            parts = error.split('"')
            if len(parts) >= 2:
                quoted = parts[1].strip().lower()

        if quoted in _LIMIT_OPERATORS:
            return (
                "BigFix session relevance has no row-limiting operator - "
                f"'{quoted}' does not exist. Use this tool's limit and offset "
                "parameters instead; the response reports total_available and "
                "truncated."
            )
        if quoted in _CLIENT_ONLY_INSPECTORS:
            return (
                f"'{quoted}' is a *client* relevance inspector, evaluated on an "
                "agent. Session relevance only sees what the root server "
                "already holds. Ask agents directly with the client_query "
                "tools instead."
            )
        return (
            "That inspector does not exist in session relevance. Session "
            "relevance uses 'bes'-prefixed objects (bes computers, bes "
            "fixlets, bes tasks, bes analyses, bes baselines, bes sites, bes "
            "actions). Inspectors that read an endpoint's disk, registry or "
            "processes are *client* relevance - use the client_query tools."
        )

    if "singular expression" in lowered:
        return (
            "A singular expression matched nothing or matched more than one "
            "object. Use the plural form and filter it, e.g. 'bes computers "
            "whose (name of it = \"HOST\")' rather than 'the bes computer'."
        )

    return ""


def check_relevance_envelope(envelope: dict) -> dict:
    """Raise ToolError if a /api/query JSON envelope reports a relevance error."""
    if "error" in envelope:
        error = envelope["error"]
        hint = _relevance_hint(str(error))
        message = f"Session relevance error: {error}"
        if hint:
            message = f"{message}\n\nHint: {hint}"
        raise ToolError(message)
    return envelope


def bes_errors(context: str):
    """Decorator translating besapi/requests exceptions into ToolError.

    ToolError messages are always shown to the MCP client, while raw
    exceptions may be masked. Never include credentials in messages.
    Works on both sync and async functions.
    """

    def _translate(err: Exception) -> ToolError:
        if isinstance(err, PermissionError):
            return ToolError(
                f"{context}: BigFix returned 403 Forbidden - the configured "
                f"operator lacks permission for this request. {_snippet(err)}"
            )
        if isinstance(err, requests.exceptions.HTTPError):
            body = ""
            if err.response is not None:
                body = f" Response: {_snippet(err.response.text)}"
            return ToolError(f"{context}: BigFix HTTP error: {_snippet(err)}.{body}")
        if isinstance(err, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return ToolError(f"{context}: cannot reach the BigFix root server: {_snippet(err)}")
        if isinstance(err, json.JSONDecodeError):
            return ToolError(f"{context}: BigFix returned a non-JSON response: {_snippet(err.doc)}")
        if isinstance(err, (ValueError, RuntimeError)):
            # includes connection.ConnectionConfigError - message is self-explanatory
            return ToolError(f"{context}: {_snippet(err)}")
        return ToolError(f"{context}: unexpected error: {_snippet(err)}")

    _handled = (
        PermissionError,
        requests.exceptions.RequestException,
        json.JSONDecodeError,
        ValueError,
        AttributeError,
        KeyError,
        RuntimeError,
    )

    def decorator(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except ToolError:
                    raise
                except _handled as err:
                    raise _translate(err) from err

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ToolError:
                raise
            except _handled as err:
                raise _translate(err) from err

        return wrapper

    return decorator
