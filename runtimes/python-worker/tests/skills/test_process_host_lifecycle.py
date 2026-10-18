from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.skills.capability_proxy import SkillCapabilityProxy
from shuliu_runtime.skills.process_host import SkillProcessError, SkillProcessHost
from shuliu_runtime.skills.sandbox import SandboxPolicy
from shuliu_runtime.tools.broker import (
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)
from tests.fakes.fake_skill_sandbox import TestOnlyProcessProvider


def _host() -> SkillProcessHost:
    return SkillProcessHost(TestOnlyProcessProvider())


def _script(tmp_path: Path, source: str) -> Path:
    package = tmp_path / "skill-package"
    package.mkdir(exist_ok=True)
    path = package / "main.py"
    path.write_text(source, encoding="utf-8")
    return package


def _run(
    package: Path,
    input_value: object,
    *,
    timeout: float,
    capability_proxy=None,
    cancel_event: threading.Event | None = None,
):
    policy = SandboxPolicy(
        skill_id="test-skill",
        permissions=tuple(
            capability_proxy.approved_permissions if capability_proxy else ()
        ),
    )
    return _host().run(
        package,
        policy,
        input_value,
        timeout=timeout,
        capability_proxy=capability_proxy,
        cancel_event=cancel_event,
    )


def _capability_proxy(broker):
    class Registry:
        def get(self, tool_id):
            if tool_id != "foundation.echo":
                raise KeyError(tool_id)
            return SimpleNamespace(permissions=("foundation.echo",))

    return SkillCapabilityProxy(
        broker=broker,
        registry=Registry(),
        actor={"subject_id": "host-agent", "organization_id": "org-1"},
        context={"organization_id": "org-1", "trace_id": "test-trace"},
        approved_permissions=("foundation.echo",),
        idempotency_key="test-run-idempotency",
    )


def test_real_subprocess_runs_with_separate_pid_and_stripped_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SHULIU_SECRET", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-leak")
    host = _host()
    package = _script(
        tmp_path,
        "import json, os, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'skill.result','output':{"
        "'pid':os.getpid(),'secret':os.getenv('SHULIU_SECRET'),"
        "'database':os.getenv('DATABASE_URL'),'cwd':os.getcwd()}}))\n",
    )
    result = host.run(
        package,
        SandboxPolicy(skill_id="test-skill", permissions=()),
        {},
        timeout=3,
    )

    assert result.output["pid"] != os.getpid()
    assert result.output["secret"] is None
    assert result.output["database"] is None
    assert Path(result.output["cwd"]).exists() is False


def test_skill_subprocess_tool_call_round_trips_through_capability_proxy(
    tmp_path: Path,
):
    class Broker:
        def __init__(self):
            self.calls = []

        def execute(self, tool_id, args, actor, context, *, idempotency_key):
            self.calls.append((tool_id, args, actor, context, idempotency_key))
            return ToolResult(
                tool_id=tool_id,
                tool_version="1.0.0",
                execution_status=ToolExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.VERIFIED,
                output={"ok": True},
                error_code=None,
                policy_decision=PolicyOutcome.ALLOW,
                policy_version="test-v1",
                policy_rule_id="test-rule",
            )

    broker = Broker()
    proxy = _capability_proxy(broker)
    result = _run(
        _script(
            tmp_path,
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "print(json.dumps({'type':'tool.request','request_id':'r1',"
            "'tool_id':'foundation.echo','args':{'text':'hello'}}), flush=True)\n"
            "reply=json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'type':'skill.result','output':reply}), flush=True)\n",
        ),
        {},
        timeout=3,
        capability_proxy=proxy,
    )

    assert len(broker.calls) == 1
    assert broker.calls[0][0] == "foundation.echo"
    assert result.output["type"] == "tool.response"


def test_timeout_terminates_child_and_cleans_temporary_directory(tmp_path: Path):
    package = _script(
        tmp_path,
        "import json, os, sys, time\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'skill.progress','output':{'cwd':os.getcwd()}}), flush=True)\n"
        "time.sleep(30)\n",
    )

    with pytest.raises(SkillProcessError) as timed_out:
        _run(package, {}, timeout=0.2)

    assert timed_out.value.code == "SKILL_EXECUTION_TIMEOUT"
    assert timed_out.value.working_directory is not None
    assert not timed_out.value.working_directory.exists()


def test_abnormal_exit_is_reported_and_resources_are_removed(tmp_path: Path):
    package = _script(tmp_path, "import sys\nsys.stdin.readline()\nsys.exit(17)\n")

    with pytest.raises(SkillProcessError) as exited:
        _run(package, {}, timeout=3)

    assert exited.value.code == "SKILL_PROCESS_EXITED"
    assert exited.value.working_directory is not None
    assert not exited.value.working_directory.exists()


def test_cancellation_terminates_child_and_cleans_resources(tmp_path: Path):
    cancel = threading.Event()
    package = _script(
        tmp_path,
        "import json, os, sys, time\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'skill.progress','output':{'cwd':os.getcwd()}}), flush=True)\n"
        "time.sleep(30)\n",
    )
    result: list[BaseException] = []

    def run():
        try:
            _run(package, {}, timeout=5, cancel_event=cancel)
        except Exception as error:  # noqa: BLE001 - preserve worker-thread failure
            result.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.2)
    cancel.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], SkillProcessError)
    assert result[0].code == "SKILL_EXECUTION_CANCELLED"
    assert result[0].working_directory is not None
    assert not result[0].working_directory.exists()


def test_timeout_is_enforced_while_child_does_not_read_large_initial_input(
    tmp_path: Path,
):
    package = _script(
        tmp_path,
        "import json, sys, time\n"
        "time.sleep(0.5)\n"
        "sys.stdin.buffer.read()\n"
        "print(json.dumps({'type':'skill.result','output':{'ok':True}}), flush=True)\n",
    )
    started = time.monotonic()

    with pytest.raises(SkillProcessError) as timed_out:
        _run(package, {"payload": "x" * 900_000}, timeout=0.1)

    assert timed_out.value.code == "SKILL_EXECUTION_TIMEOUT"
    assert time.monotonic() - started < 0.4


def test_tool_broker_hang_cannot_outlive_skill_deadline(tmp_path: Path):
    class HangingProxy:
        def execute(self, *_args, **_kwargs):
            time.sleep(2)

    package = _script(
        tmp_path,
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'tool.request','request_id':'r1',"
        "'tool_id':'foundation.echo','args':{}}), flush=True)\n"
        "reply=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'type':'skill.result','output':reply}), flush=True)\n",
    )
    started = time.monotonic()

    with pytest.raises(SkillProcessError) as timed_out:
        _run(
            package,
            {},
            timeout=0.2,
            capability_proxy=_capability_proxy(HangingProxy()),
        )

    assert timed_out.value.code == "SKILL_TOOL_EXECUTION_UNKNOWN"
    assert time.monotonic() - started < 1.2
