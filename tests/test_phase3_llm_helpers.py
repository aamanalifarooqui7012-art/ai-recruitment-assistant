import pytest

from interview.errors import LLMOutputError
from interview.llm import parse_json_object, strictify


def test_parse_plain_fenced_and_wrapped_json():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here you go: {"a": {"b": 2}} done') == {"a": {"b": 2}}


@pytest.mark.parametrize("bad", ["", "   ", "no json here", "[1, 2]", "{broken"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(LLMOutputError):
        parse_json_object(bad)


def test_strictify_closes_every_object_and_requires_all_fields():
    schema = {
        "title": "X", "type": "object",
        "properties": {"title": {"type": "string", "default": "t"}, "inner": {"$ref": "#/$defs/Inner"}},
        "$defs": {"Inner": {"title": "Inner", "type": "object", "properties": {"n": {"type": "number"}}}},
    }
    out = strictify(schema)
    assert out["additionalProperties"] is False and out["required"] == ["title", "inner"]
    assert "title" in out["properties"] and "default" not in out["properties"]["title"]
    assert out["$defs"]["Inner"]["additionalProperties"] is False and out["$defs"]["Inner"]["required"] == ["n"]
