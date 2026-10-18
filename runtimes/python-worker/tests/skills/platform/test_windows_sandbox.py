from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from shuliu_runtime.skills import process_host
from shuliu_runtime.skills.process_host import SkillProcessError, SkillProcessHost
from shuliu_runtime.skills.sandbox import SandboxPolicy, UnavailableSandboxProvider
from tests.fakes.fake_skill_sandbox import TestOnlyProcessProvider


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")
def test_windows_without_verified_provider_fails_closed():
    assessment = UnavailableSandboxProvider().assess(
        SandboxPolicy(skill_id="platform-check", permissions=())
    )

    assert assessment.supported is False
    assert assessment.enforced_controls == frozenset()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")
def test_windows_job_is_assigned_before_skill_can_spawn_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    descendant_started = tmp_path / "descendant-started"
    descendant_survived = tmp_path / "descendant-survived"
    package = tmp_path / "windows-skill"
    package.mkdir()
    script = package / "main.py"
    child_code = (
        "import pathlib,time; "
        f"pathlib.Path({str(descendant_started)!r}).write_text('started'); "
        "time.sleep(0.8); "
        f"pathlib.Path({str(descendant_survived)!r}).write_text('survived')"
    )
    script.write_text(
        "import os, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(0.1)\n"
        "os.abort()\n",
        encoding="utf-8",
    )

    original_attach = process_host._attach_windows_job

    def delay_job_assignment_until_skill_would_have_started(process):
        deadline = time.monotonic() + 0.25
        while not descendant_started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return original_attach(process)

    monkeypatch.setattr(
        process_host,
        "_attach_windows_job",
        delay_job_assignment_until_skill_would_have_started,
    )
    with pytest.raises(SkillProcessError):
        SkillProcessHost(TestOnlyProcessProvider()).run(
            package,
            SandboxPolicy(skill_id="windows-race", permissions=()),
            {},
            timeout=3,
        )

    time.sleep(1)
    assert not descendant_survived.exists()
