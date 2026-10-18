from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from shuliu_runtime.skills.sandbox import (
    REQUIRED_SANDBOX_CONTROLS,
    SandboxAssessment,
    SandboxPolicy,
)


class TestOnlyProcessProvider:
    """Test harness only; this does not enforce or prove any OS sandbox control."""

    def assess(self, _policy: SandboxPolicy) -> SandboxAssessment:
        return SandboxAssessment(
            supported=True,
            enforced_controls=REQUIRED_SANDBOX_CONTROLS,
            reason="test-only process lifecycle harness",
        )

    def wrap_command(
        self,
        command: Sequence[str],
        _policy: SandboxPolicy,
        _working_directory: Path,
    ) -> Sequence[str]:
        return command
