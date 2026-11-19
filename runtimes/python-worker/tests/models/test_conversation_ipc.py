from __future__ import annotations

import json
from io import StringIO
from threading import Event
from uuid import uuid4

import pytest

from shuliu_runtime.ipc.server import IpcRequestCancelled
from shuliu_runtime.models.conversation import create_conversation_ipc_handlers
from shuliu_runtime.models.config import ProviderProfile
from shuliu_runtime.models.conversation import ConversationContext, ConversationService
from shuliu_runtime.models.gateway import ModelGateway, ProviderResult
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.ipc.server import IpcServer
from tests.fakes.fake_model import MemoryBudget, MemoryHealth, MemoryModelCalls


def test_conversation_ipc_accepts_only_fixed_chat_request_fields():
    class Runtime:
        def complete_chat(self, **values):
            return {"status": "succeeded", "trace_id": values["trace_id"]}

    handler = create_conversation_ipc_handlers(Runtime())["chat.complete"]
    trace_id = str(uuid4())
    request_id = str(uuid4())
    result = handler(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek/deepseek-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "request_id": request_id,
            "trace_id": trace_id,
            "external_processing_confirmed": True,
        },
        Event(),
    )

    assert result == {"status": "succeeded", "trace_id": trace_id}


def test_conversation_ipc_rejects_renderer_supplied_authorization_context():
    class Runtime:
        def __init__(self):
            self.called = False

        def complete_chat(self, **values):
            self.called = True
            return {"status": "succeeded"}

    runtime = Runtime()
    handler = create_conversation_ipc_handlers(runtime)["chat.complete"]
    with pytest.raises(ValueError):
        handler(
            {
                "provider_id": "deepseek",
                "model_id": "deepseek/deepseek-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "request_id": str(uuid4()),
                "trace_id": str(uuid4()),
                "external_processing_confirmed": True,
                "permissions": ["admin"],
            },
            Event(),
        )
    assert runtime.called is False


def test_conversation_ipc_honors_cancellation_before_calling_runtime():
    class Runtime:
        def complete_chat(self, **values):
            raise AssertionError("cancelled request reached conversation service")

    cancellation = Event()
    cancellation.set()
    handler = create_conversation_ipc_handlers(Runtime())["chat.complete"]
    with pytest.raises(IpcRequestCancelled):
        handler(
            {
                "provider_id": "deepseek",
                "model_id": "deepseek/deepseek-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "request_id": str(uuid4()),
                "trace_id": str(uuid4()),
                "external_processing_confirmed": True,
            },
            cancellation,
        )


def test_ndjson_server_routes_chat_through_gateway_with_fake_provider():
    class Adapter:
        def __init__(self):
            self.calls = 0

        def invoke(self, provider, request, api_key=None):
            self.calls += 1
            assert provider.provider_id == "fixture"
            assert request.organization_id == "ipc-test-org"
            return ProviderResult(
                output="reply from fake Gateway adapter",
                usage={"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
                cost=0.0001,
                model_version=provider.model,
            )

    profile = ProviderProfile(
        provider_id="fixture",
        model="fixture/chat",
        capabilities=frozenset({"fast_chat"}),
        priority=1,
        is_local=True,
        secret_ref=None,
    )
    adapter = Adapter()
    gateway = ModelGateway(
        providers=(profile.to_model_provider_config(),),
        adapter=adapter,
        policy=PolicyEngine(
            [PolicyRule(
                rule_id="fixture-chat",
                action="model.invoke.fast_chat.fixture",
                risk="LOW",
                decision="ALLOW",
                organization_id="ipc-test-org",
                permission="foundation.model.chat",
                resource_type="model_provider",
                classification="PUBLIC",
                privacy="PUBLIC",
            )],
            policy_version="ipc-chat-test-v1",
        ),
        budget=MemoryBudget(),
        health=MemoryHealth(("fixture",)),
        model_calls=MemoryModelCalls(),
        fallback_enabled=False,
    )
    conversation = ConversationService(
        (profile,),
        gateway,
        ConversationContext(
            organization_id="ipc-test-org",
            subject_id="ipc-test-user",
            permissions=("foundation.model.chat",),
            budget_ref="budget://ipc-test-org/chat",
        ),
    )

    class Runtime:
        def complete_chat(self, **values):
            return conversation.complete(
                values["provider_id"], values["model_id"], values["messages"], values["request_id"],
                values["trace_id"], values["external_processing_confirmed"],
            )

    request_id = str(uuid4())
    trace_id = str(uuid4())
    frame = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": str(uuid4()),
        "request_id": str(uuid4()),
        "trace_id": trace_id,
        "message_type": "request",
        "payload": {
            "method": "chat.complete",
            "params": {
                "provider_id": "fixture",
                "model_id": "fixture/chat",
                "messages": [{"role": "user", "content": "hello"}],
                "request_id": request_id,
                "trace_id": trace_id,
                "external_processing_confirmed": True,
            },
        },
    }
    output = StringIO()
    IpcServer(create_conversation_ipc_handlers(Runtime())).serve(
        input_stream=StringIO(json.dumps(frame) + "\n"), output_stream=output, error_stream=StringIO()
    )

    response = json.loads(output.getvalue())
    result = response["payload"]["result"]
    assert result["status"] == "succeeded"
    assert result["response"] == "reply from fake Gateway adapter"
    assert result["trace_id"] == trace_id
    assert result["request_status"] == "http_response_received"
    assert result["usage"]["total_tokens"] == 6
    assert adapter.calls == 1
