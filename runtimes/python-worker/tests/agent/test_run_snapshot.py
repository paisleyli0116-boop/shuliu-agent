from __future__ import annotations

import pytest
from shuliu_runtime.agent.run import AgentCore
from shuliu_runtime.agent.snapshot import InvalidVersionSnapshot

from tests.agent.test_state_transitions import InMemoryAgentRepository, _clock


def test_agent_run_pins_agent_and_resolved_component_versions():
    repository = InMemoryAgentRepository()
    core = AgentCore(repository=repository, clock=_clock)
    task = core.create_task(
        task_id="task-versions", organization_id="org-1", trace_id="trace-versions"
    )
    current_versions = {
        "skill:generic": "1.0.0",
        "workflow:generic": "1.4.0",
        "policy": "policy-v3",
        "runtime": "runtime-v0.1.0",
        "protocol.agent": "1",
    }

    run = core.create_run(
        task,
        agent_version="agent-v2.0.0",
        resolved_versions=current_versions,
    )
    current_versions["skill:generic"] = "1.1.0"

    assert run.snapshot.agent_version == "agent-v2.0.0"
    assert dict(run.snapshot.resolved_versions) == {
        "skill:generic": "1.0.0",
        "workflow:generic": "1.4.0",
        "policy": "policy-v3",
        "runtime": "runtime-v0.1.0",
        "protocol.agent": "1",
    }
    with pytest.raises(TypeError):
        run.snapshot.resolved_versions["skill:generic"] = "1.2.0"
    assert run.trace_id == task.trace_id == "trace-versions"


def test_invalid_version_snapshot_does_not_create_a_run():
    repository = InMemoryAgentRepository()
    core = AgentCore(repository=repository, clock=_clock)
    task = core.create_task(
        task_id="task-invalid-versions", organization_id="org-1", trace_id="trace-1"
    )

    with pytest.raises(InvalidVersionSnapshot):
        core.create_run(task, agent_version="agent-1.0.0", resolved_versions={"": "1"})

    assert repository.runs == {}


def test_run_snapshot_does_not_drift_when_later_versions_change():
    repository = InMemoryAgentRepository()
    core = AgentCore(repository=repository, clock=_clock)
    task = core.create_task(
        task_id="task-drift", organization_id="org-1", trace_id="trace-drift"
    )
    versions = {"skill:generic": "1.0.0", "workflow:generic": "1.0.0"}
    run = core.create_run(task, agent_version="agent-1.0.0", resolved_versions=versions)
    versions.update({"skill:generic": "2.0.0", "workflow:generic": "2.0.0"})

    assert dict(run.snapshot.resolved_versions) == {
        "skill:generic": "1.0.0",
        "workflow:generic": "1.0.0",
    }
    assert repository.runs[run.run_id].snapshot == run.snapshot
