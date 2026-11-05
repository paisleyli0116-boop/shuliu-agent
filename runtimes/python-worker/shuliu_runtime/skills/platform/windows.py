"""Windows Skill sandbox provider.

Evidence-backed assessment of the production Windows containment
mechanism used by `shuliu_runtime.skills.process_host`: suspended process
creation, Job Object assignment before the first instruction runs, and
Job kill-on-close cleanup (including descendant containment and breakaway
denial — proven by `tests/skills/platform/test_sandbox_attacks.py` and the
race tests in `test_windows_sandbox.py`).

A Job Object does NOT enforce filesystem scope, network scope, secret
isolation, Foundation DB isolation, or OS-command denial for a same-user
process; the attack tests record real successful attacks for each of
those controls.  This provider therefore reports only `separate_process`
as enforced and keeps executable Skills disabled on Windows until an OS
mechanism that proves the remaining controls is validated (per ADR-025,
fail closed).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..sandbox import SandboxAssessment, SandboxPolicy, SandboxUnavailableError

_ENFORCED_CONTROLS = frozenset({"separate_process"})

_UNPROVEN_CONTROLS = (
    "filesystem_scope",
    "network_scope",
    "secret_isolation",
    "foundation_db_isolation",
    "no_direct_os_control",
)

_REASON = (
    "Windows Job Object containment proves only separate_process "
    "(suspended spawn, bind-before-resume, descendant containment, "
    "breakaway denial, kill-on-close). Runner attack tests succeeded "
    "against every unproven control: reading unauthorized files, opening "
    "network sockets, running OS commands, reading same-user secret and "
    "Foundation DB files. Executable Skills stay disabled on Windows "
    "until those controls are enforced by a validated OS mechanism. "
    f"Unproven controls: {', '.join(_UNPROVEN_CONTROLS)}."
)


class WindowsJobSandboxProvider:
    """Fail-closed Windows provider reporting only proven OS controls."""

    def assess(self, policy: SandboxPolicy) -> SandboxAssessment:
        del policy
        return SandboxAssessment(
            supported=False,
            enforced_controls=_ENFORCED_CONTROLS,
            reason=_REASON,
        )

    def wrap_command(
        self, command: Sequence[str], policy: SandboxPolicy, working_directory: Path
    ) -> Sequence[str]:
        del command, policy, working_directory
        raise SandboxUnavailableError(
            "SKILL_SANDBOX_UNAVAILABLE",
            "Windows Skill sandbox does not enforce the required controls; "
            "executable Skills must not start",
        )
