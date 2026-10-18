from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class WorkerProtocolError(ValueError):
    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(f"worker protocol message is invalid ({code})")
        self.code = code
        self.path = path


def _validator() -> Draft202012Validator:
    repository_root = Path(__file__).resolve().parents[4]
    schema_root = repository_root / "packages" / "protocols" / "schemas"
    common = json.loads((schema_root / "common-v1.schema.json").read_text(encoding="utf-8"))
    worker = json.loads((schema_root / "worker-v1.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(worker, registry=registry)


_WORKER_VALIDATOR = _validator()


def validate_worker_message(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise WorkerProtocolError("SCHEMA_VALIDATION_FAILED", "/")
    version = value.get("protocol_version")
    if isinstance(version, int) and not isinstance(version, bool) and version != 1:
        raise WorkerProtocolError("PROTOCOL_VERSION_UNSUPPORTED", "/protocol_version")
    errors = sorted(_WORKER_VALIDATOR.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        path = "/" + "/".join(str(part) for part in errors[0].absolute_path)
        raise WorkerProtocolError("SCHEMA_VALIDATION_FAILED", path)


@dataclass(frozen=True)
class WorkerMessage:
    message_id: str
    request_id: str
    trace_id: str
    message_type: str
    payload: Mapping[str, Any] = field(repr=False)
    protocol_version: int = 1

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> WorkerMessage:
        validate_worker_message(value)
        return cls(
            message_id=value["message_id"],
            request_id=value["request_id"],
            trace_id=value["trace_id"],
            message_type=value["message_type"],
            payload=dict(value["payload"]),
            protocol_version=value["protocol_version"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_name": "worker",
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "message_type": self.message_type,
            "payload": dict(self.payload),
        }
