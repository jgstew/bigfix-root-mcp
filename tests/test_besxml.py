"""XML -> dict conversion.

Replaces besapi's `RESTResult.besdict` / `elem2dict`, which has two defects
confirmed against a live BigFix 11 root server:

1. Repeated sibling elements holding *text* crash it: it does
   `result[key].copy()` to promote a scalar to a list, and `str` has no
   `.copy()`. `GET /api/computer/{id}` returns repeated
   `<Property Name="...">value</Property>` elements, so it raises
   `AttributeError: 'str' object has no attribute 'copy'` on any real
   computer record.
2. It discards attributes entirely - which for a computer record throws away
   the `Name` attribute that says *which* property each value belongs to.

Upstream candidate: see docs/besapi-proposals.md.
"""

from bigfix_root_mcp import besxml

COMPUTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<BESAPI xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Computer Resource="https://bes.example.com:52311/api/computer/7">
    <ID>7</ID>
    <LastReportTime>Thu, 26 May 2022 10:48:03 -0400</LastReportTime>
    <Property Name="Computer Name">HYPERV</Property>
    <Property Name="OS">Win2019 10.0.17763.7009</Property>
    <Property Name="CPU">3100 MHz Xeon</Property>
  </Computer>
</BESAPI>"""


class TestElementToDict:
    def test_repeated_text_siblings_become_a_list(self):
        """The exact case that crashes besapi's elem2dict."""
        out = besxml.xml_to_dict("<r><a>one</a><a>two</a><a>three</a></r>")
        assert out["r"]["a"] == ["one", "two", "three"]

    def test_single_child_is_not_wrapped_in_a_list(self):
        out = besxml.xml_to_dict("<r><a>one</a></r>")
        assert out["r"]["a"] == "one"

    def test_attributes_are_preserved(self):
        out = besxml.xml_to_dict('<r><a Name="OS">Win11</a></r>')
        assert out["r"]["a"] == {"@Name": "OS", "#text": "Win11"}

    def test_attribute_only_element(self):
        out = besxml.xml_to_dict('<r><a Resource="http://x/1"/></r>')
        assert out["r"]["a"] == {"@Resource": "http://x/1"}

    def test_namespace_prefixes_are_stripped(self):
        out = besxml.xml_to_dict('<r xmlns:bes="http://x"><bes:a>v</bes:a></r>')
        assert out["r"]["a"] == "v"

    def test_nested_structure(self):
        out = besxml.xml_to_dict("<r><a><b>v</b></a></r>")
        assert out["r"]["a"]["b"] == "v"

    def test_empty_element_is_empty_dict(self):
        assert besxml.xml_to_dict("<r><a/></r>")["r"]["a"] == {}

    def test_real_computer_record_keeps_property_names(self):
        out = besxml.xml_to_dict(COMPUTER_XML)
        computer = out["BESAPI"]["Computer"]
        assert computer["ID"] == "7"
        assert computer["@Resource"].endswith("/api/computer/7")
        props = {p["@Name"]: p["#text"] for p in computer["Property"]}
        assert props["Computer Name"] == "HYPERV"
        assert props["OS"].startswith("Win2019")

    def test_accepts_bytes(self):
        assert besxml.xml_to_dict(b"<r><a>v</a></r>")["r"]["a"] == "v"

    def test_entities_are_not_resolved(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "x">]><r><a>&e;</a></r>'
        out = besxml.xml_to_dict(bomb)
        assert out["r"]["a"] != "x"
