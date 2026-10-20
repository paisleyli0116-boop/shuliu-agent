from __future__ import annotations

import os
import subprocess
import time
from uuid import uuid4

import pytest
import win32gui


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Interactive User Session only")


def test_interactive_session_executes_and_verifies_a_real_uia_action():
    from shuliu_runtime.approval.service import ApprovalError
    from shuliu_runtime.computer.adapter import ComputerRuntime
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.policy.risk import RiskLevel
    from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
    from shuliu_runtime.tools.broker import IdentityPrincipal
    from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
    from pywinauto import Desktop

    class Identity:
        def resolve(self, actor):
            if actor.get("subject_id") != "task25-e2e":
                return None
            return IdentityPrincipal("task25-e2e", "org-task25", ("computer.read", "computer.act"))

    class Approval:
        def consume(self, approval_id, action_context):
            if approval_id != "task25-approved":
                raise ApprovalError("APPROVAL_NOT_FOUND", "approval is unavailable")

    evidence = []

    def save_evidence(record):
        evidence.append(record)
        return f"task25-evidence-{len(evidence)}"

    rules = [
        PolicyRule("computer-read", "computer.capability", RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.read"),
        PolicyRule("computer-observe", "computer.observe", RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.read"),
        PolicyRule("computer-act", "computer.act", RiskLevel.CRITICAL,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.act"),
    ]
    runtime = ComputerRuntime(
        identity=Identity(),
        policy=PolicyEngine(rules, policy_version="task25-windows-e2e-v1"),
        approvals=Approval(),
        idempotency_store=InMemoryIdempotencyStore(),
        event_sink=lambda _event: None,
        evidence_sink=save_evidence,
    )
    actor = {"subject_id": "task25-e2e"}
    context = {
        "organization_id": "org-task25",
        "trace_id": "trace-task25-windows-e2e",
        "privacy": "INTERNAL",
        "approval_id": "task25-approved",
        "target_approver_id": "task25-local-reviewer",
        "action_summary": "Type a non-sensitive marker into the task-owned WinForms window",
    }
    process = None
    try:
        capability = runtime.capability(actor=actor, context=context)
        if capability.output["status"] != "AVAILABLE":
            reason = (
                "COMPUTER_RUNTIME_NOT_EXECUTABLE: Windows runner has no unlocked "
                "Interactive User Session; Computer Use was not exercised"
            )
            if os.environ.get("SHULIU_REQUIRE_INTERACTIVE_COMPUTER") == "1":
                pytest.fail(reason)
            pytest.skip(reason)

        title = f"ShuLiu Task 25 Fixture {uuid4()}"
        script = "; ".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "$form = [System.Windows.Forms.Form]::new()",
                f"$form.Text = '{title}'",
                "$form.Width = 720",
                "$form.Height = 420",
                "$edit = [System.Windows.Forms.TextBox]::new()",
                "$edit.Name = 'Task25Edit'",
                "$edit.AccessibleName = 'Document'",
                "$edit.Multiline = $true",
                "$edit.Left = 12",
                "$edit.Top = 12",
                "$edit.Width = 680",
                "$edit.Height = 340",
                "$form.Controls.Add($edit)",
                "[System.Windows.Forms.Application]::Run($form)",
            ]
        )
        process = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        window_handle = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("Task-owned WinForms fixture exited before showing a window")
            matches = []

            def find_window(hwnd, _extra):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
                    matches.append(hwnd)
                    return False
                return True

            win32gui.EnumWindows(find_window, None)
            if matches:
                window_handle = matches[0]
                break
            time.sleep(0.2)
        else:
            raise AssertionError("Task-owned WinForms fixture window did not become visible")
        assert window_handle is not None
        window_ref = f"0x{window_handle:x}"
        observed = runtime.observe(window_ref, actor=actor, context=context)
        assert observed.output.get("status") == "OBSERVED", observed.output
        assert observed.output["trust"] == "UNTRUSTED_UI_CONTENT"
        edit = next(
            item for item in observed.output["elements"]
            if item["role"] == "Edit" and not item["sensitive"]
        )
        marker = "ShuLiu Task 25 UIA smoke"
        result = runtime.act(
            {
                "kind": "type",
                "window_ref": window_ref,
                "observation_id": observed.output["observation_id"],
                "element_ref": edit["element_ref"],
                "text": marker,
            },
            actor=actor,
            context=context,
            idempotency_key="task25-notepad-type-1",
        )

        assert result.output["status"] == "VERIFIED"
        assert result.verification_status.value == "VERIFIED"
        window = Desktop(backend="uia").window(handle=window_handle).wrapper_object()
        editor = next(control for control in window.descendants(control_type="Edit"))
        assert marker in editor.get_value()
        assert evidence and all(item["session"]["state"] == "AVAILABLE" for item in evidence)
        assert all(item["machine"] and item["session"]["session_id"] is not None for item in evidence)
    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
