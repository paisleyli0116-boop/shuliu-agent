from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, text

from app.knowledge.embedding import GatewayEmbeddingAdapter, load_embedding_profile
from app.knowledge.ingest import KnowledgeChunkInput, KnowledgeDocumentInput, ingest_document
from app.knowledge.retrieval import KnowledgeAuthorizationContext, KnowledgeRetriever
from app.persistence.postgres import create_postgres_engine, knowledge_embedding_profiles


SERVICE_ROOT = Path(__file__).resolve().parents[2]


class _ConfiguredHealth:
    def __init__(self, provider_id: str) -> None:
        self._provider_id = provider_id

    def is_healthy(self, provider_id: str) -> bool:
        return provider_id == self._provider_id


class _NoopInvalidator:
    def invalidate_document(self, _organization_id: str, _document_id: str) -> None:
        return None


@pytest.mark.real_embedding
def test_real_embedding_provider_round_trips_through_gateway_pgvector_and_budget():
    if os.environ.get("SHULIU_REAL_EMBEDDING_TEST") != "1":
        pytest.skip("real embedding request requires SHULIU_REAL_EMBEDDING_TEST=1")
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.fail("real embedding test requires configured PostgreSQL", pytrace=False)

    # Imports and OS credential resolution happen only after explicit test opt-in.
    from shuliu_runtime.models.config import load_provider_profiles
    from shuliu_runtime.models.gateway import ModelGateway
    from shuliu_runtime.models.postgres_usage import PostgresModelBudget, PostgresModelCallSink
    from shuliu_runtime.models.providers.litellm_adapter import LiteLLMAdapter
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.policy.rules import PolicyRule
    from shuliu_runtime.security.os_secret_store import OSSecretStore
    from shuliu_runtime.security.secret_store import SecretResolver

    profile = load_embedding_profile()
    configured = tuple(
        item
        for item in load_provider_profiles()
        if item.provider_id == profile.provider_id and item.enabled
    )
    if len(configured) != 1:
        pytest.fail("configured real embedding provider profile is missing", pytrace=False)
    provider_profile = configured[0]
    if (
        provider_profile.model != profile.model
        or "embedding" not in provider_profile.capabilities
        or provider_profile.is_local
        or not isinstance(provider_profile.secret_ref, str)
        or not provider_profile.secret_ref.startswith("secret://os/")
        or "PUBLIC" not in profile.privacy_levels
    ):
        pytest.fail("real embedding provider configuration does not match the index profile", pytrace=False)
    try:
        secret_store = OSSecretStore()
    except Exception:
        pytest.fail("approved OS SecretRef storage is unavailable", pytrace=False)

    engine = create_postgres_engine(database_url)
    organization_id = f"task30-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    trace_ingest = f"task30-ingest-{uuid4().hex}"
    trace_query = f"task30-query-{uuid4().hex}"
    budget_ref = f"budget://{organization_id}/real-embedding"
    budget = PostgresModelBudget(engine)
    model_calls = PostgresModelCallSink(engine)
    try:
        with engine.begin() as connection:
            config = Config(str(SERVICE_ROOT / "services" / "cloud-api" / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("0.25"),
            reservation_amount=Decimal("0.05"),
        )
        model_provider = provider_profile.to_model_provider_config()
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id=f"task30-embedding-{organization_id}",
                    action=f"model.invoke.embedding.{profile.provider_id}",
                    risk="LOW",
                    decision="ALLOW",
                    organization_id=organization_id,
                    permission="foundation.model.invoke",
                    resource_type="model_provider",
                    classification="PUBLIC",
                    privacy="PUBLIC",
                )
            ],
            policy_version="task30-real-embedding-v1",
        )
        gateway = ModelGateway(
            providers=(model_provider,),
            adapter=LiteLLMAdapter(timeout_seconds=30.0),
            policy=policy,
            budget=budget,
            health=_ConfiguredHealth(profile.provider_id),
            model_calls=model_calls,
            secret_resolver=SecretResolver({"os": secret_store}),
            fallback_enabled=False,
        )
        embedder = GatewayEmbeddingAdapter(gateway, profile)
        document = KnowledgeDocumentInput(
            document_id=document_id,
            organization_id=organization_id,
            scope="team:task30-test",
            classification="PUBLIC",
            metadata={"source": "fixed public Task 30 integration fixture"},
            chunks=(
                KnowledgeChunkInput(
                    "ShuLiu Task 30 public embedding retrieval verification.",
                    {"source": "task30-public-fixture"},
                ),
            ),
            permission_codes=("knowledge.read",),
            subject_id="task30-test-operator",
            permissions=("foundation.model.invoke", "knowledge.read"),
            privacy="PUBLIC",
            budget_ref=budget_ref,
            trace_id=trace_ingest,
        )
        ingest_document(engine, document, embedder, _NoopInvalidator())
        context = KnowledgeAuthorizationContext(
            organization_id=organization_id,
            subject_id="task30-test-operator",
            scopes=("team:task30-test",),
            classifications=("PUBLIC",),
            permissions=("foundation.model.invoke", "knowledge.read"),
            knowledge_permissions=("knowledge.read",),
            privacy="PUBLIC",
            budget_ref=budget_ref,
            trace_id=trace_query,
        )
        hits = KnowledgeRetriever(engine, embedder).retrieve(
            "ShuLiu Task 30 public embedding retrieval verification.", context, 5
        )
        assert len(hits) == 1 and hits[0].document_id == document_id

        with engine.connect() as connection:
            call_rows = connection.execute(
                text(
                    "SELECT call_id, provider_id, model, usage, cost FROM model_calls "
                    "WHERE organization_id = :organization_id "
                    "AND requested_capability = 'embedding' AND status = 'SUCCEEDED' "
                    "AND trace_id IN (:trace_ingest, :trace_query)"
                ),
                {
                    "organization_id": organization_id,
                    "trace_ingest": trace_ingest,
                    "trace_query": trace_query,
                },
            ).mappings().all()
            reservation_rows = connection.execute(
                text(
                    "SELECT call_id, usage, settled_amount FROM model_budget_reservations "
                    "WHERE organization_id = :organization_id AND budget_ref = :budget_ref "
                    "AND capability = 'embedding' AND status = 'SETTLED'"
                ),
                {"organization_id": organization_id, "budget_ref": budget_ref},
            ).mappings().all()
        account = budget.get_account(organization_id, budget_ref)
        assert len(call_rows) == 2
        assert len(reservation_rows) == 2
        costs_by_call = {}
        for call in call_rows:
            assert call["provider_id"] == profile.provider_id
            assert call["model"] == profile.model
            assert call["cost"] is not None
            costs_by_call[call["call_id"]] = (Decimal(call["cost"]), dict(call["usage"]))
        settlements_by_call = {
            reservation["call_id"]: (
                Decimal(reservation["settled_amount"]),
                dict(reservation["usage"]),
            )
            for reservation in reservation_rows
        }
        assert costs_by_call == settlements_by_call
        total_cost = sum((cost for cost, _usage in costs_by_call.values()), Decimal("0"))
        assert account is not None and account.reserved_amount == 0
        assert account.spent_amount == total_cost
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM knowledge_documents WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
            connection.execute(
                delete(knowledge_embedding_profiles).where(
                    knowledge_embedding_profiles.c.organization_id == organization_id
                )
            )
            connection.execute(
                text("DELETE FROM model_budget_reservations WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("DELETE FROM model_calls WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("DELETE FROM model_budget_accounts WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
        engine.dispose()
