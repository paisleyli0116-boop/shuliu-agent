import json
import pytest
from pathlib import Path
from jsonschema import Draft202012Validator

from .support import call, make_runtime, opened


def test_real_and_fake_adapters_use_the_shared_browser_tool_payload_schema(tmp_path):
    path = Path(__file__).resolve().parents[4] / "packages/protocols/schemas/browser-tool-v1.schema.json"
    assert path.is_file(), "Task 24 Browser Tool payload schema is missing"
    validator = Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))
    runtime, _, _, evidence, _ = make_runtime(tmp_path)
    try:
        session, _ = opened(runtime)
        result = call(runtime, "observe", {"session_id": session})
        validator.validate(result.output)
        assert not validator.is_valid({**result.output, "cookies": "secret"})
        assert not validator.is_valid({**result.output, "outcome_unknown": True})
        assert evidence
    finally:
        runtime.close()


@pytest.mark.parametrize("field,limit", [("text", 8000), ("label", 256)])
def test_redaction_remains_within_shared_contract_bounds(tmp_path, field, limit):
    runtime, backend, *_ = make_runtime(tmp_path)
    try:
        session, _ = opened(runtime)
        value = ("token=x " * (limit // 8 + 1))[:limit]
        if field == "text":
            backend.page["text"] = value
        else:
            backend.page["elements"][0]["label"] = value
        result = call(runtime, "observe", {"session_id": session})
        view = result.output["observation"]
        rendered = view["text"] if field == "text" else view["elements"][0]["label"]
        assert len(rendered) <= limit
        assert "token=x" not in rendered
    finally:
        runtime.close()
