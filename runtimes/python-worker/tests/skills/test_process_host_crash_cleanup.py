from __future__ import annotations

from pathlib import Path

import pytest
from shuliu_runtime.skills.process_host import SkillProcessError, SkillProcessHost
from shuliu_runtime.skills.sandbox import SandboxPolicy
from tests.fakes.fake_skill_sandbox import TestOnlyProcessProvider


def test_crashed_skill_process_is_reaped_and_temp_resources_are_removed(tmp_path: Path):
    descendant_marker = tmp_path / "descendant-survived"
    package = tmp_path / "crashing-skill"
    package.mkdir()
    entrypoint = package / "main.py"
    entrypoint.write_text(
        "import json, os, signal, subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', \"import pathlib,time; time.sleep(0.8); pathlib.Path({str(descendant_marker)!r}).write_text('survived')\"])\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'skill.progress','output':{'cwd':os.getcwd()}}), flush=True)\n"
        "os.abort() if os.name == 'nt' else os.kill(os.getpid(), signal.SIGKILL)\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillProcessError) as crashed:
        SkillProcessHost(TestOnlyProcessProvider()).run(
            package,
            SandboxPolicy(skill_id="test-crash", permissions=()),
            {},
            timeout=3,
        )

    assert crashed.value.code == "SKILL_PROCESS_CRASHED"
    assert crashed.value.return_code is not None
    assert crashed.value.return_code < 0 or crashed.value.return_code in {3, 134, 139}
    assert crashed.value.working_directory is not None
    assert not crashed.value.working_directory.exists()
    import time

    time.sleep(1)
    assert not descendant_marker.exists()
