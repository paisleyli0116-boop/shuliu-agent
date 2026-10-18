from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from shuliu_runtime.skills.capability_proxy import SkillCapabilityProxy
from shuliu_runtime.skills.process_host import (
    SkillProcessError,
    SkillProcessHost,
    _run_supervised_process,
)
from shuliu_runtime.skills.sandbox import (
    SandboxPolicy,
    SandboxUnavailableError,
    UnavailableSandboxProvider,
    validate_skill_permissions,
)


def test_unavailable_os_sandbox_denies_skill_before_starting_child(tmp_path: Path):
    marker = tmp_path / "skill-ran"
    secret_file = tmp_path / "secret.txt"
    database_file = tmp_path / "foundation.sqlite"
    secret_file.write_text("secret-value", encoding="utf-8")
    database_file.write_text("database-bytes", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text(
        "import os, pathlib, socket, subprocess\n"
        f"pathlib.Path({str(secret_file)!r}).read_text()\n"
        f"pathlib.Path({str(database_file)!r}).read_text()\n"
        "os.environ.get('DATABASE_URL')\n"
        "socket.create_connection(('example.invalid', 443), timeout=0.1)\n"
        "subprocess.run(['/bin/echo', 'os-command'])\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    host = SkillProcessHost(sandbox_provider=UnavailableSandboxProvider())

    with pytest.raises(SandboxUnavailableError) as denied:
        host.run(
            package,
            SandboxPolicy(skill_id="sample", permissions=()),
            {},
            timeout=1,
        )

    assert denied.value.code == "SKILL_SANDBOX_UNAVAILABLE"
    assert not marker.exists()


def test_policy_rejects_unapproved_permission_increase():
    with pytest.raises(SandboxUnavailableError) as denied:
        validate_skill_permissions(
            requested=("foundation.read", "network.any"),
            approved=("foundation.read",),
        )

    assert denied.value.code == "SKILL_PERMISSION_ESCALATION"


def test_process_host_rejects_proxy_permissions_beyond_approved_skill_policy(
    tmp_path: Path,
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text("pass\n", encoding="utf-8")
    host = SkillProcessHost(sandbox_provider=UnavailableSandboxProvider())

    class Broker:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("permission escalation must stop before the broker")

    class Registry:
        def get(self, _tool_id):
            return SimpleNamespace(permissions=("foundation.admin",))

    proxy = SkillCapabilityProxy(
        broker=Broker(),
        registry=Registry(),
        actor={"subject_id": "agent", "organization_id": "org-1"},
        context={"organization_id": "org-1", "trace_id": "trace-1"},
        approved_permissions=("foundation.admin",),
        idempotency_key="host-run",
    )

    with pytest.raises(SandboxUnavailableError) as denied:
        host.run(
            package,
            SandboxPolicy(skill_id="sample", permissions=("foundation.read",)),
            {},
            timeout=1,
            capability_proxy=proxy,
        )

    assert denied.value.code == "SKILL_PERMISSION_ESCALATION"


def test_process_host_rejects_non_foundation_tool_proxy(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text("pass\n", encoding="utf-8")

    with pytest.raises(SandboxUnavailableError) as denied:
        SkillProcessHost().run(
            package,
            SandboxPolicy(skill_id="sample", permissions=("foundation.read",)),
            {},
            timeout=1,
            capability_proxy=SimpleNamespace(
                approved_permissions=frozenset({"foundation.read"}),
                dispatch=lambda _frame: {"output": "bypassed"},
            ),
        )

    assert denied.value.code == "SKILL_TOOL_BROKER_UNAVAILABLE"


def test_low_level_supervisor_requires_host_validation_token(tmp_path: Path):
    with pytest.raises(SkillProcessError) as denied:
        _run_supervised_process(
            {},
            timeout=1,
            command=("must-not-start",),
            working_directory=tmp_path,
            _launch_token=object(),
        )

    assert denied.value.code == "SKILL_SANDBOX_UNAVAILABLE"


def test_skill_policy_never_accepts_secret_values_or_database_paths(tmp_path: Path):
    with pytest.raises(SandboxUnavailableError) as secret:
        SandboxPolicy(
            skill_id="sample",
            permissions=(),
            secret_refs=("provider.api_key",),
        )
    assert secret.value.code == "SKILL_SECRET_ACCESS_DENIED"

    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text("pass\n", encoding="utf-8")
    foundation_database = tmp_path / "foundation.sqlite"
    foundation_database.write_text("", encoding="utf-8")
    with pytest.raises(SandboxUnavailableError) as database:
        SkillProcessHost(sandbox_provider=UnavailableSandboxProvider()).run(
            package,
            SandboxPolicy(
                skill_id="sample",
                permissions=(),
                filesystem_roots=(foundation_database,),
            ),
            {},
            timeout=1,
        )
    assert database.value.code == "SKILL_FOUNDATION_DATA_ACCESS_DENIED"


def test_sandbox_assessment_must_cover_every_required_control():
    host = SkillProcessHost(sandbox_provider=UnavailableSandboxProvider())

    with pytest.raises(SandboxUnavailableError):
        host._require_enforced_controls(
            supported=True,
            enforced_controls=frozenset({"process_isolation"}),
        )
