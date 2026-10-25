"""Driver Chrome launch options must keep the Chromium sandbox on by default.

Hosted CI runners (Linux containers, macOS) often lack the Chromium SUID sandbox
helper, which makes a sandboxed headless Chrome fail to start or hang. An opt-in
environment flag (SHULIU_BROWSER_ALLOW_NO_SANDBOX=1) lets CI relax the sandbox
explicitly; the default for real users stays sandboxed. The probe runs driver.py
in a subprocess because importing it redirects sys.stdout at module scope.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DRIVER_PATH = Path(__file__).resolve().parents[2] / "shuliu_runtime" / "browser" / "driver.py"

PROBE = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("shuliu_browser_driver_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.__stdout__.write(json.dumps(module.launch_options()))
"""


def _launch_options(env: dict[str, str] | None = None) -> dict:
    import os

    merged = {k: v for k, v in os.environ.items() if k != "SHULIU_BROWSER_ALLOW_NO_SANDBOX"}
    merged.update(env or {})
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, str(DRIVER_PATH)],
        env=merged,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(completed.stdout)


def test_sandbox_stays_enabled_by_default():
    options = _launch_options()

    assert options["chromium_sandbox"] is True
    assert "--no-sandbox" not in options["args"]
    assert "--disable-setuid-sandbox" not in options["args"]


def test_opt_in_flag_disables_sandbox_for_ci_runners_only():
    options = _launch_options({"SHULIU_BROWSER_ALLOW_NO_SANDBOX": "1"})

    assert options["chromium_sandbox"] is False
    assert "--no-sandbox" in options["args"]
    assert "--disable-setuid-sandbox" in options["args"]
    assert "--disable-dev-shm-usage" in options["args"]


def test_baseline_launch_arguments_are_preserved():
    options = _launch_options()
    hardened = _launch_options({"SHULIU_BROWSER_ALLOW_NO_SANDBOX": "1"})

    assert "--no-first-run" in options["args"]
    assert "--no-default-browser-check" in options["args"]
    for argument in options["args"]:
        assert argument in hardened["args"]
