"""Bigfix-root-mcp: a minimal read-only MCP server around the besapi library."""

from importlib.metadata import PackageNotFoundError, version

# `[project] version` in pyproject.toml is the single source of truth; this
# reads it back out of the installed package metadata so there is no second
# place to bump.
try:
    __version__ = version("bigfix-root-mcp")
except PackageNotFoundError:  # pragma: no cover - only hit for an uninstalled checkout
    __version__ = "0.0.0"
