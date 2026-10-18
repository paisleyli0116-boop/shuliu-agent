from __future__ import annotations

import importlib
import importlib.util


class FakeTransport:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, query: str, top_k: int):
        self.calls.append((query, top_k))
        return self.results


def test_knowledge_client_delegates_remote_retrieval_without_local_index() -> None:
    module_name = "shuliu_runtime.knowledge.client"
    try:
        module_spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        module_spec = None
    assert module_spec is not None, "Cloud-backed Knowledge client is not implemented"
    module = importlib.import_module(module_name)
    KnowledgeClient = module.KnowledgeClient
    KnowledgeResult = module.KnowledgeResult
    result = KnowledgeResult("chunk-1", "doc-1", "authorized content", {"page": 2}, 0.1)
    transport = FakeTransport([result])
    client = KnowledgeClient(transport)

    assert client.retrieve("policy", 3) == (result,)
    assert transport.calls == [("policy", 3)]
