"""BigFix REST XML -> plain dict.

Used instead of besapi's `RESTResult.besdict`, which is built on `elem2dict`
and has two defects confirmed against a live BigFix 11 root server:

1. It crashes on repeated sibling elements holding text. To promote a scalar
   to a list it calls `result[key].copy()`, and `str` has no `.copy()`. Since
   `GET /api/computer/{id}` returns repeated `<Property Name="...">value</...>`
   elements, `besdict` raises `AttributeError` on any real computer record.
2. It discards attributes, which for a computer record throws away the `Name`
   attribute identifying *which* property each value is.

Upstream candidate (see docs/besapi-proposals.md): fixing `elem2dict` in
besapi would let this module be deleted. No fastmcp imports, pure functions.

Output shape:
  - text-only element        -> the string
  - element with attributes  -> {"@Attr": ..., "#text": ...}
  - repeated sibling tags    -> a list under one key
  - empty element            -> {}
Namespace prefixes are stripped, matching besapi's behavior.
"""

import lxml.etree


def _strip_namespace(tag) -> str:
    tag = str(tag)
    return tag.split("}")[1] if "}" in tag else tag


def element_to_value(element):
    """Convert one lxml element to a string or dict."""
    result = {}

    for name, value in element.attrib.items():
        result[f"@{_strip_namespace(name)}"] = value

    children = {}
    for child in element.iterchildren():
        # skip comments and processing instructions, which have callable tags
        if not isinstance(child.tag, str):
            continue
        key = _strip_namespace(child.tag)
        value = element_to_value(child)
        if key in children:
            if isinstance(children[key], list):
                children[key].append(value)
            else:
                # promote scalar to list - besapi's `.copy()` here is the bug
                children[key] = [children[key], value]
        else:
            children[key] = value

    text = (element.text or "").strip()

    if children:
        result.update(children)
        if text:
            result["#text"] = text
        return result
    if text:
        if result:
            result["#text"] = text
            return result
        return text
    return result


def parse_xml(xml):
    """Parse BigFix REST XML to an lxml root element, safely.

    Entity resolution and network access are disabled: this parses responses
    from a server whose contents are influenced by managed endpoints. A fresh
    parser per call, since lxml parsers are not safe to share across threads.
    """
    data = xml.encode() if isinstance(xml, str) else bytes(xml)
    parser = lxml.etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return lxml.etree.fromstring(data, parser=parser)


def xml_to_dict(xml) -> dict:
    """Parse BigFix REST XML into {root_tag: value}."""
    root = parse_xml(xml)
    return {_strip_namespace(root.tag): element_to_value(root)}
