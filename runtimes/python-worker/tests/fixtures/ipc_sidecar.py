from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Mapping

from shuliu_runtime.ipc.server import IpcRequestCancelled, IpcServer
from shuliu_runtime.models.config import ProviderProfile
from shuliu_runtime.models.conversation import ConversationContext, ConversationService, create_conversation_ipc_handlers
from shuliu_runtime.models.gateway import ModelGateway, ProviderResult
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule


def _echo(params: Mapping[str, Any], _: threading.Event) -> dict[str, Any]:
    return {"value": params.get("value")}


def _delay(params: Mapping[str, Any], cancelled: threading.Event) -> dict[str, Any]:
    if cancelled.wait(float(params.get("seconds", 0.25))):
        raise IpcRequestCancelled
    return {"status": "finished"}


def _crash(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
    os._exit(23)


def _environment(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
    return {
        "sensitive_variable_present": "SHULIU_SECRET_SENTINEL" in os.environ,
        "stdin_encoding": sys.stdin.encoding.lower(),
        "stdout_encoding": sys.stdout.encoding.lower(),
    }


class _FixtureBudget:
    def reserve(self, organization_id, budget_ref, provider_id, capability, trace_id, *, call_id):
        return True

    def settle(self, organization_id, budget_ref, call_id, usage, cost):
        return None

    def release(self, organization_id, budget_ref, call_id):
        return None

    def mark_unknown(self, organization_id, budget_ref, call_id):
        return None


class _FixtureHealth:
    def is_healthy(self, provider_id: str) -> bool:
        return provider_id == "fixture"


class _FixtureCallSink:
    def record(self, call):
        return None


class _FixtureAdapter:
    def invoke(self, provider, request, api_key=None):
        return ProviderResult(
            output="fixture reply",
            usage={"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            cost=0.0001,
            model_version=provider.model,
        )


_FIXTURE_PROFILE = ProviderProfile(
    provider_id="fixture",
    model="fixture/chat",
    capabilities=frozenset({"fast_chat"}),
    priority=1,
    is_local=True,
    secret_ref=None,
)
_FIXTURE_GATEWAY = ModelGateway(
    providers=(_FIXTURE_PROFILE.to_model_provider_config(),),
    adapter=_FixtureAdapter(),
    policy=PolicyEngine(
        [PolicyRule(
            rule_id="fixture-chat",
            action="model.invoke.fast_chat.fixture",
            risk="LOW",
            decision="ALLOW",
            organization_id="local",
            permission="foundation.model.chat",
            resource_type="model_provider",
            classification="PUBLIC",
            privacy="PUBLIC",
        )],
        policy_version="ipc-fixture-v1",
    ),
    budget=_FixtureBudget(),
    health=_FixtureHealth(),
    model_calls=_FixtureCallSink(),
    fallback_enabled=False,
)
_FIXTURE_CONVERSATION = ConversationService(
    (_FIXTURE_PROFILE,),
    _FIXTURE_GATEWAY,
    ConversationContext(
        organization_id="local",
        subject_id="electron-ipc-fixture",
        permissions=("foundation.model.chat",),
        budget_ref="budget://local/electron-ipc-fixture",
    ),
)


class _FixtureConversationRuntime:
    def complete_chat(self, **values):
        return _FIXTURE_CONVERSATION.complete(
            values["provider_id"],
            values["model_id"],
            values["messages"],
            values["request_id"],
            values["trace_id"],
            values["external_processing_confirmed"],
        )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "incompatible":
        request = json.loads(sys.stdin.readline())
        response = {
            "protocol_name": "runtime-ipc",
            "protocol_version": 2,
            "message_id": "incompatible-message",
            "request_id": request["request_id"],
            "trace_id": request["trace_id"],
            "message_type": "response",
            "payload": {"result": {"protocol_version": 2, "status": "ready"}},
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        return

    # The supervisor must not forward arbitrary stderr fields into normal logs.
    sys.stderr.write('{"level":"INFO","event":"fixture_start","code":"IPC_READY","authorization":"fixture-secret-marker"}\n')
    sys.stderr.flush()
    IpcServer({
        "test.echo": _echo,
        "test.delay": _delay,
        "test.crash": _crash,
        "test.environment": _environment,
        **create_conversation_ipc_handlers(_FixtureConversationRuntime()),
    }).serve()


if __name__ == "__main__":
    main()
