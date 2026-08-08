"""Computer and content read helpers built on besapi.

Upstream candidate: everything here is generic BigFix REST logic taking `conn`
as its first argument, with no fastmcp imports, raising ValueError /
requests.HTTPError in besapi style. See docs/besapi-proposals.md.

Every REST path in this module was confirmed against a live BigFix 11 root
server via /api/help rather than inferred from documentation; the relevance
expressions were likewise executed against a live server before being shipped.
See docs/rest-endpoints.md.
"""

import urllib.parse

import besapi.besapi
import lxml.etree

from bigfix_root_mcp import besxml

# kind -> (REST path prefix, session relevance plural inspector)
CONTENT_KINDS = {
    "fixlet": ("fixlet", "bes fixlets"),
    "task": ("task", "bes tasks"),
    "analysis": ("analysis", "bes analyses"),
    "baseline": ("baseline", "bes baselines"),
}


def validate_path_segment(path: str, label: str = "site_path") -> str:
    """Validate and percent-encode a multi-segment REST path component.

    Site paths legitimately contain "/" ("custom/MySite", "operator/Bob"), so
    this splits on "/" and validates each segment rather than treating the
    whole thing as one component.

    Rejects "." / ".." / empty segments outright instead of encoding them: a
    dot segment is never a real site name, and whether it escapes /api/
    depends on how the root server normalizes paths - not something to leave
    to chance. Everything else is percent-encoded, so a "?" cannot graft a
    query string onto the request.

    Each segment is decoded *before* it is checked and then re-encoded. Two
    reasons, both load-bearing:

    - Checking the raw segment would let "%2e%2e" through, since it only
      becomes ".." at the server.
    - Site names may legitimately contain "/" - a real deployment has
      `custom/Public%2fWindows`, one site named "Public/Windows". Decoding
      then re-encoding keeps that a single segment and makes the function
      idempotent, so an already-encoded path from a REST Resource URL can be
      passed straight back in without double-encoding it.
    """
    if not path or not path.strip():
        raise ValueError(f"{label} must not be empty.")

    encoded = []
    for segment in path.split("/"):
        decoded = urllib.parse.unquote(segment)
        if not decoded.strip():
            raise ValueError(f"{label} contains an empty segment: {path!r}")
        if decoded.strip() in (".", ".."):
            raise ValueError(
                f"{label} must not contain '.' or '..' path segments " f"(encoded or not): {path!r}"
            )
        encoded.append(urllib.parse.quote(decoded, safe=""))
    return "/".join(encoded)


def _check_status(result):
    """Raise requests.HTTPError on non-2xx; besapi only raises on 403."""
    result.request.raise_for_status()
    return result


def _int_id(value, label: str) -> int:
    """Coerce an id to int, so it can never carry path syntax."""
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{label} must be an integer, got {value!r}.") from err


def _relevance_literal(term: str) -> str:
    """Lowercase a search term for use inside a relevance string literal.

    BigFix relevance has no portable escape for a double quote inside a string
    literal, so a term containing one is refused rather than silently turned
    into an expression that means something else.
    """
    term = str(term)
    if '"' in term:
        raise ValueError(
            "Search term must not contain a double quote character - "
            "BigFix relevance string literals cannot escape it."
        )
    return term.lower()


def build_computer_search_relevance(name_contains: str) -> str:
    """Session relevance finding computers by case-insensitive name substring.

    Note there is no relevance-level row limit in this dialect (`first` /
    `firsts` / `items` are all undefined against a BigFix 11 root server), so
    the caller is responsible for bounding the response.
    """
    term = _relevance_literal(name_contains)
    return (
        "(name of it, id of it, last report time of it as string) "
        "of bes computers whose "
        f'(name of it as lowercase contains "{term}")'
    )


def build_content_search_relevance(kind: str, name_contains: str) -> str:
    """Session relevance finding content of one kind by name substring."""
    if kind not in CONTENT_KINDS:
        raise ValueError(f"kind must be one of {sorted(CONTENT_KINDS)}, got {kind!r}.")
    _, inspector = CONTENT_KINDS[kind]
    term = _relevance_literal(name_contains)
    return (
        "(name of it, id of it, name of site of it) "
        f"of {inspector} whose "
        f'(name of it as lowercase contains "{term}")'
    )


def validate_bes_xml(bes_xml) -> dict:
    """Check BES XML against the schemas besapi ships.

    No server call.

    Parsed with entity resolution and network access disabled rather than
    lxml's defaults, because this accepts XML from the model: besapi's own
    validate_xsd uses a default parser, so well-formedness is checked here
    first and validate_xsd only ever sees XML that already parsed safely.
    """
    data = bes_xml.encode() if isinstance(bes_xml, str) else bytes(bes_xml)

    # a fresh parser per call - lxml parsers are not safe to share
    parser = lxml.etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        lxml.etree.fromstring(data, parser=parser)
    except lxml.etree.XMLSyntaxError as err:
        return {"valid": False, "reason": f"not well-formed XML: {err}"}

    if besapi.besapi.validate_xsd(data):
        return {"valid": True, "reason": ""}
    return {
        "valid": False,
        "reason": (
            "well-formed, but does not validate against any of BES.xsd, "
            "BESAPI.xsd or BESActionSettings.xsd."
        ),
    }


def build_site_path_map(conn) -> dict:
    """Map site display name -> list of REST site paths, from /api/sites.

    Session relevance can report a content item's site *name* but there is no
    site *path* inspector (`site path`, `path of site`, `type of site` are all
    undefined on a BigFix 11 root server), and the REST API addresses content
    by path. The Resource URL in /api/sites is the authoritative source:

        <ActionSite   Resource=".../api/site/master">
        <ExternalSite Resource=".../api/site/external/BES%20Support">
        <CustomSite   Resource=".../api/site/custom/Public%2fWindows">
        <OperatorSite Resource=".../api/site/operator/Bob">

    Paths are kept exactly as the server encoded them, which matters for the
    site literally named "Public/Windows".

    A name maps to a *list* because nothing stops a custom and an external
    site sharing a display name.
    """
    result = _check_status(conn.get("sites"))
    mapping: dict = {}
    root = besxml.parse_xml(result.text)
    for element in root.iterchildren():
        if not isinstance(element.tag, str):
            continue
        resource = element.attrib.get("Resource", "")
        if "/api/site/" not in resource:
            continue
        path = resource.split("/api/site/", 1)[1]
        name_element = element.find("Name")
        name = name_element.text if name_element is not None else None
        if name:
            mapping.setdefault(name, []).append(path)
    return mapping


def annotate_content_rows(rows: list, site_paths: dict) -> list:
    """Turn (name, id, site name) relevance rows into dicts with a site_path.

    site_path is null when the name is unknown or ambiguous rather than
    guessed; the candidates are listed so the caller can pick.
    """
    annotated = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            annotated.append(row)
            continue
        name, content_id, site_name = row
        candidates = site_paths.get(site_name, [])
        entry = {
            "name": name,
            "id": content_id,
            "site_name": site_name,
            "site_path": candidates[0] if len(candidates) == 1 else None,
        }
        if len(candidates) > 1:
            entry["site_path_candidates"] = candidates
        annotated.append(entry)
    return annotated


def get_computer(conn, computer_id):
    """GET /api/computer/{id}."""
    return conn.get(f"computer/{_int_id(computer_id, 'computer_id')}")


def get_computer_fixlets(conn, computer_id):
    """GET /api/computer/{id}/fixlets - content currently relevant to a computer."""
    return conn.get(f"computer/{_int_id(computer_id, 'computer_id')}/fixlets")


def get_content(conn, kind: str, site_path: str, content_id):
    """GET /api/{kind}/{site_path}/{id} for fixlet, task, analysis or baseline."""
    if kind not in CONTENT_KINDS:
        raise ValueError(f"kind must be one of {sorted(CONTENT_KINDS)}, got {kind!r}.")
    prefix, _ = CONTENT_KINDS[kind]
    safe_site = validate_path_segment(site_path)
    return conn.get(f"{prefix}/{safe_site}/{_int_id(content_id, 'content_id')}")


def list_operators(conn):
    """GET /api/operators."""
    return conn.get("operators")


def list_roles(conn):
    """GET /api/roles."""
    return conn.get("roles")


def get_computer_group_by_name(conn, group_name: str, site_path: str) -> dict | None:
    """Find a computer group by name within an explicit site path.

    Returns None when not found. Implemented against
    /api/computergroups/{site_path} directly rather than besapi's
    get_computergroup, which routes through the mutable "current site path"
    connection state this package avoids.
    """
    safe_site = validate_path_segment(site_path)
    # status must be checked before touching besobj: a 404 body is not XML,
    # and parsing it would surface as a syntax error rather than the HTTP error
    result = _check_status(conn.get(f"computergroups/{safe_site}"))
    groups = getattr(result.besobj, "ComputerGroup", None)
    if groups is not None:
        for group in groups:
            if group_name == str(group.Name):
                return {
                    "name": group_name,
                    "site_path": site_path,
                    "resource": group.attrib.get("Resource", ""),
                }
    return None
