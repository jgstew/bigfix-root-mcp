"""Advisory static analysis of relevance via bigfix_relevance_analyzer.

Everything here is advisory. preflight() returns None on a clean query and
*never raises* - a bug in the analyzer must never break a call to the real
server, so its whole body is fenced. The analyzer's inspector table is a
snapshot, not a specification: unknown-inspector stays a warning, and nothing
in this module ever gates a request to the root server.

This module must not import fastmcp (same layering rule as clientquery.py):
functions raise ValueError for caller mistakes and server.py translates.
"""

import logging

from bigfix_relevance_analyzer import (
    Dialect,
    InspectorKind,
    LintConfig,
    analyze_relevance,
    inspectors,
    is_definite,
    lint_analysis,
)

logger = logging.getLogger(__name__)

# advisory payloads ride on every tool response, so keep them small
MAX_FINDINGS = 5
MAX_UNKNOWN_REFERENCES = 20
MAX_REFERENCES = 50
MAX_SEARCH_LIMIT = 25

# the dialects a caller may name; the analyzer's UNCERTAIN/BOTH are verdicts,
# never valid requests
_DIALECTS = {
    Dialect.SESSION.value: Dialect.SESSION,
    Dialect.CLIENT.value: Dialect.CLIENT,
}

_ADVISORY_NOTE = (
    "Advisory static analysis only; the server result is authoritative. "
    "'unknown-inspector' means the name is not in the analyzer's snapshot, "
    "not that it does not exist on the server."
)


def _parse_dialect(dialect: str | None) -> Dialect | None:
    if dialect is None:
        return None
    parsed = _DIALECTS.get(dialect.strip().lower())
    if parsed is None:
        raise ValueError(f"dialect must be one of {sorted(_DIALECTS)}, got {dialect!r}")
    return parsed


def _finding_dict(finding) -> dict:
    out = {
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "line": finding.line,
    }
    if finding.suggestions:
        out["suggestions"] = list(finding.suggestions)
    return out


def _dialect_mismatch(report, pinned: Dialect) -> str | None:
    """A one-line note when the statement's own evidence contradicts the pin.

    Two independent signals: the pre-parse text classifier, and the inspector
    table's post-parse verdict (resolved_dialect). Either being definite and
    different from the pinned dialect is worth saying out loud.
    """
    for observed in (report.classified_dialect, report.resolved_dialect):
        if observed is not None and is_definite(observed) and observed is not pinned:
            return (
                f"Query classifies as {observed.value} relevance but this tool "
                f"evaluates {pinned.value} relevance."
            )
    return None


def preflight(relevance: str, dialect: str) -> dict | None:
    """Analyze + lint with the dialect pinned; None when nothing to say.

    Advisory: never raises, even for a bad dialect string - a caller bug here
    must not take down the server call this rides along with.
    """
    try:
        pinned = _DIALECTS.get(dialect)
        if pinned is None:
            logger.warning("preflight called with unknown dialect %r; skipping", dialect)
            return None
        report = analyze_relevance(relevance, dialect=pinned)
        findings = lint_analysis(report, LintConfig(dialect=pinned, suggest=True))
        mismatch = _dialect_mismatch(report, pinned)
        if not findings and not mismatch:
            return None
        out: dict = {
            "findings": [_finding_dict(f) for f in findings[:MAX_FINDINGS]],
            "note": _ADVISORY_NOTE,
        }
        if len(findings) > MAX_FINDINGS:
            out["findings_truncated"] = len(findings) - MAX_FINDINGS
        if mismatch:
            out["dialect_mismatch"] = mismatch
    except Exception:
        logger.exception("relevance preflight failed; continuing without it")
        return None
    return out


def error_appendix(advisory: dict | None) -> str:
    """Render an advisory dict as text for a server-side relevance error.

    Returns "" when there is nothing to add.
    """
    if not advisory:
        return ""
    lines = []
    for finding in advisory.get("findings", []):
        line = f"- {finding['code']}: {finding['message']}"
        # unknown-inspector messages already say "did you mean" when suggest=True;
        # only append suggestions the message does not already carry
        missing = [s for s in finding.get("suggestions", ()) if s not in finding["message"]]
        if missing:
            line += " (did you mean: " + ", ".join(missing) + ")"
        lines.append(line)
    if advisory.get("dialect_mismatch"):
        lines.append(f"- {advisory['dialect_mismatch']}")
    return "\n".join(lines)


def analyze(
    relevance: str,
    dialect: str | None,
    platform: str | None,
    include_sexpr: bool = False,
) -> dict:
    """Curated analysis report for the standalone analyze_relevance tool.

    Deliberately not the analyzer's full to_dict(): list fields are capped and
    node-level detail is dropped, because this rides back to an LLM client.
    """
    pinned = _parse_dialect(dialect)
    report = analyze_relevance(relevance, dialect=pinned, platform=platform)
    findings = lint_analysis(report, LintConfig(dialect=pinned, suggest=True))
    unknown = report.unknown_references
    references = [{"phrase": ref.reference.phrase, "known": ref.known} for ref in report.references]
    out: dict = {
        "parsed": report.parsed,
        "parse_error": None if report.parse_error is None else str(report.parse_error),
        "dialect": {
            "effective": report.dialect.value,
            "classified": (
                None if report.classified_dialect is None else report.classified_dialect.value
            ),
            "resolved": (
                None if report.resolved_dialect is None else report.resolved_dialect.value
            ),
            "assumed": report.dialect_assumed,
        },
        "platforms": sorted(report.platforms),
        "complexity_score": report.complexity.score,
        "findings": [_finding_dict(f) for f in findings],
        "unknown_references": list(unknown[:MAX_UNKNOWN_REFERENCES]),
        "unbound_its": len(report.unbound_its),
        "references": references[:MAX_REFERENCES],
        "note": _ADVISORY_NOTE,
    }
    if len(unknown) > MAX_UNKNOWN_REFERENCES:
        out["unknown_references_truncated"] = len(unknown) - MAX_UNKNOWN_REFERENCES
    if len(references) > MAX_REFERENCES:
        out["references_truncated"] = len(references) - MAX_REFERENCES
    if include_sexpr:
        out["sexpr"] = report.sexpr
    return out


def search(query: str, dialect: str | None, kind: str | None, limit: int) -> dict:
    """Search the inspector snapshot; small wire-shaped rows, best match first."""
    pinned = _parse_dialect(dialect)
    parsed_kind = None
    if kind is not None:
        try:
            parsed_kind = InspectorKind(kind.strip().lower())
        except ValueError:
            valid = sorted(k.value for k in InspectorKind)
            raise ValueError(f"kind must be one of {valid}, got {kind!r}") from None
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    results = inspectors.search(query, dialect=pinned, kind=parsed_kind, limit=limit)
    return {"matches": [result.to_dict() for result in results], "count": len(results)}
