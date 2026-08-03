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
        return text[:_SNIPPET_LEN] + "…"
    return text


def check_rest_result(result, context: str):
    """Raise ToolError if a besapi RESTResult has a non-2xx status code.

    Returns the result unchanged otherwise.
    """
    status = result.request.status_code
    if not 200 <= status < 300:
        raise ToolError(
            f"{context}: BigFix REST API returned HTTP {status}: "
            f"{_snippet(result.text)}"
        )
    return result


def check_relevance_envelope(envelope: dict) -> dict:
    """Raise ToolError if a /api/query JSON envelope reports a relevance error."""
    if "error" in envelope:
        raise ToolError(f"Session relevance error: {envelope['error']}")
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
        if isinstance(
            err, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
        ):
            return ToolError(
                f"{context}: cannot reach the BigFix root server: {_snippet(err)}"
            )
        if isinstance(err, json.JSONDecodeError):
            return ToolError(
                f"{context}: BigFix returned a non-JSON response: {_snippet(err.doc)}"
            )
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
