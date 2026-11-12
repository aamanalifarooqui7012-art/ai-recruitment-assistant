from interview.taxonomy import SkillTaxonomy


def test_aliases_map_to_one_id():
    t = SkillTaxonomy.from_yaml()
    for name in ["React", "ReactJS", "React.js", "react js"]:
        assert t.canonical(name) == ("react", True)
    assert t.canonical("Node.js")[0] == "nodejs"
    assert t.canonical("NodeJS")[0] == "nodejs"


def test_unknown_skill_keeps_slug_and_is_flagged():
    assert SkillTaxonomy.from_yaml().canonical("Zzzlang") == ("zzzlang", False)
