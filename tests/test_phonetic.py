"""RED tests: phonetic-family relationships built from KanjiVG kvg:phon data.

Relationships are source-traceable to KanjiVG's own kvg:phon marker (never
inferred). Families group the kanji that share a phonetic component, order
members usefully (by frequency rank, then stable codepoint), and record a
compact deterministic source/attribution. Nothing is invented when the source
marker is absent.
"""
import bees_kanji as bk


def test_phon_extracted_from_kvg_phon_attribute():
    svg = (
        '<svg xmlns:kvg="x"><g kvg:element="\u6642" kvg:phon="\u5bfa">'
        '<g kvg:element="\u65e5"/><g kvg:element="\u5bfa"/></g></svg>'
    )
    assert bk.extract_phonetic_component(svg, "\u6642") == "\u5bfa"  # 時 -> 寺


def test_phon_absent_returns_none_never_invented():
    svg = '<svg xmlns:kvg="x"><g kvg:element="\u751f"/></svg>'  # 生, no kvg:phon
    assert bk.extract_phonetic_component(svg, "\u751f") is None


def test_phon_ignores_self_referential_marker():
    # A kvg:phon that equals the character itself is not a family relationship.
    svg = '<svg xmlns:kvg="x"><g kvg:element="\u5bfa" kvg:phon="\u5bfa"/></svg>'
    assert bk.extract_phonetic_component(svg, "\u5bfa") is None


def test_build_families_groups_shared_component_and_orders_by_rank():
    # 時(rank 200) 持(rank 400) 待(rank 100) all share phonetic 寺.
    phon_map = {"\u6642": "\u5bfa", "\u6301": "\u5bfa", "\u5f85": "\u5bfa"}
    ranks = {"\u6642": 200, "\u6301": 400, "\u5f85": 100}
    fams = bk.build_phonetic_families(phon_map, ranks)
    assert "\u5bfa" in fams
    members = fams["\u5bfa"]["members"]
    # ordered by rank ascending: 待(100), 時(200), 持(400)
    assert members == ["\u5f85", "\u6642", "\u6301"]
    # source/attribution recorded compactly and deterministically
    assert fams["\u5bfa"]["source"] == "KanjiVG"


def test_build_families_drops_singletons():
    # A phonetic component with only one member is not a family.
    phon_map = {"\u6821": "\u4ea4"}  # 校 -> 交, alone
    fams = bk.build_phonetic_families(phon_map, {"\u6821": 50})
    assert fams == {}


def test_family_member_order_stable_on_missing_rank():
    # Members lacking a rank sort after ranked ones, by codepoint, deterministic.
    phon_map = {"\u6642": "\u5bfa", "\u6301": "\u5bfa", "\u5f85": "\u5bfa"}
    ranks = {"\u6642": 200}  # 持 and 待 have no rank
    fams = bk.build_phonetic_families(phon_map, ranks)
    members = fams["\u5bfa"]["members"]
    assert members[0] == "\u6642"  # ranked first
    # unranked tail ordered by codepoint: 待(U+5F85) < 持(U+6301)
    assert members[1:] == ["\u5f85", "\u6301"]


def test_family_node_lists_members_and_attribution():
    fam = {"component": "\u5bfa", "members": ["\u5f85", "\u6642", "\u6301"],
           "source": "KanjiVG"}
    node = bk.build_phonetic_family_node("\u6642", fam)
    import json
    blob = json.dumps(node, ensure_ascii=False)
    # names the shared phonetic component and the sibling members
    assert "\u5bfa" in blob            # 寺 component
    assert "\u6301" in blob and "\u5f85" in blob  # siblings shown
    # compact source attribution present
    assert "KanjiVG" in blob


def test_family_node_none_when_no_family():
    assert bk.build_phonetic_family_node("\u751f", None) is None
