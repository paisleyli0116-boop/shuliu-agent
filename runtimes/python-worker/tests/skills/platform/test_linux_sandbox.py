from __future__ import annotations

import sys

import pytest
from shuliu_runtime.skills.sandbox import SandboxPolicy, UnavailableSandboxProvider


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux runner only")
def test_linux_without_verified_provider_fails_closed():
    assessment = UnavailableSandboxProvider().assess(
        SandboxPolicy(skill_id="platform-check", permissions=())
    )

    assert assessment.supported is False
    assert assessment.enforced_controls == frozenset()
