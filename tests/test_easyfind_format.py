from rae.easyfind.format import (
    EASYFIND_EXTENSION,
    EASYFIND_FORMAT,
    EASYFIND_SCHEMA_VERSION,
    MANIFEST_PATH,
    QUICK_OPEN_PATH,
    REQUIRED_PATHS,
    dumps_json,
    dumps_jsonl_line,
    parse_jsonl,
)


def test_format_constants():
    assert EASYFIND_FORMAT == "rae-easyfind-v1"
    assert EASYFIND_SCHEMA_VERSION == 1
    assert EASYFIND_EXTENSION == ".easyfind"
    assert MANIFEST_PATH in REQUIRED_PATHS
    assert QUICK_OPEN_PATH in REQUIRED_PATHS


def test_json_helpers():
    data = {"key": "value", "count": 1}
    text = dumps_json(data)
    assert '"key": "value"' in text
    line = dumps_jsonl_line({"id": "a1"})
    assert line.endswith("\n")
    records = parse_jsonl(line + '{"id": "a2"}\n')
    assert records == [{"id": "a1"}, {"id": "a2"}]
