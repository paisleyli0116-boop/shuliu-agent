from __future__ import annotations
import time
import secrets
from pathlib import Path
from typing import Callable

try:
    from . import autopilot
    from .approvals import ApprovalLedger, can_resume_external_action as approvals_can_resume
except ImportError:
    import autopilot
    from approvals import ApprovalLedger, can_resume_external_action as approvals_can_resume


def _valid_scoped_verification(state, *, request_id, action, test_suite, run_token,
                               reserved_version, exit_code):
    if exit_code != 0 or not isinstance(state,dict):
        return False
    marker=state.get("approval_resume_in_flight")
    results=state.get("test_results")
    if not isinstance(marker,dict) or not isinstance(results,list) or len(results)!=1:
        return False
    result=results[0]
    return bool(
        int(state.get("state_version",0)) > reserved_version
        and marker.get("request_id")==request_id
        and marker.get("action")==action
        and marker.get("test_suite")==test_suite
        and marker.get("run_token")==run_token
        and marker.get("run_id")
        and marker.get("run_id")==state.get("run_id")
        and state.get("authorized_verification_request_id")==request_id
        and state.get("authorized_verification_run_id")==state.get("run_id")
        and state.get("local_verification")=="PASSED"
        and state.get("status")=="BLOCKED"
        and result.get("name")==test_suite
        and result.get("passed") is True
        and result.get("exit_code")==0
        and result.get("timed_out") is False
        and result.get("process_tree_terminated") is True
    )


def tick(state_dir: Path, *, runner: Callable | None = None, now=None) -> dict:
    state_dir=Path(state_dir)
    if autopilot.is_stopped(state_dir):
        return {"status":"STOPPED","ran":False,"reason":"emergency stop"}
    lock=autopilot.SingleInstanceLock(state_dir/"instance.lock")
    try:
        lock.acquire()
    except autopilot.InstanceAlreadyRunning:
        return {"status":"BUSY","ran":False,"reason":"another Autopilot cycle owns the checkpoint"}
    try:
        return _tick_locked(state_dir,runner=runner,now=now,lock=lock)
    finally:
        lock.release()


def _tick_locked(state_dir: Path, *, runner: Callable | None = None, now=None, lock) -> dict:
    state_dir = Path(state_dir)
    state_path = state_dir / "state.json"
    state = autopilot.load_state(state_path)
    if (state_dir / "STOP").exists():
        return {"status": "STOPPED", "ran": False, "reason": "emergency stop"}
    if (state_dir / "PAUSE").exists():
        return {"status": "PAUSED", "ran": False, "reason": "pause marker"}
    if not state:
        if runner is not None:
            return {"status":"STARTED","ran":True,"exit_code":runner(state_dir)}
        return {"status":"STARTED","ran":True,
                "exit_code":autopilot.run_cycle(state_dir,_held_lock=lock)}

    status = state.get("status")
    reservation = None
    in_flight = state.get("approval_resume_in_flight")
    if isinstance(in_flight, dict):
        if status != "WAITING_APPROVAL":
            autopilot.transition(state, "WAITING_APPROVAL",
                                 blocking_reason="Approved local recovery outcome is unknown; manual reconciliation required",
                                 requires_user_approval=True)
            autopilot.save_state(state_path, state)
        return {"status": "WAITING_APPROVAL", "ran": False, "reason": "recovery outcome unknown; refusing replay"}
    if status == "WAITING_APPROVAL":
        resume = state.get("approved_resume")
        if not isinstance(resume, dict) or resume.get("kind") != "safe_local_verification":
            return {"status": status, "ran": False, "reason": "no safe resume action"}
        request_id = resume.get("request_id")
        task = state.get("task_id")
        action = resume.get("action")
        scope = resume.get("scope")
        if not all(isinstance(x, str) and x for x in (request_id, task, action, scope)):
            return {"status": status, "ran": False, "reason": "invalid resume checkpoint"}
        if action not in {"repair_resume", "codex_service_recovery"}:
            return {"status": status, "ran": False, "reason": "approval action has no automatic local verification handler"}
        ledger = ApprovalLedger(state_dir / "approvals.json")
        approval = ledger.get(request_id)
        if (not approval or not resume.get("test_suite")
                or resume.get("test_suite") != approval.get("test_suite")):
            return {"status": status, "ran": False, "reason": "approved test suite does not match the persisted authorization"}
        if (approval and approval.get("status")=="OUTCOME_UNKNOWN" and approval.get("uses")==1
                and approval.get("task")==task and approval.get("action")==action and approval.get("scope")==scope):
            state["blocking_reason"]="A single-use approval was consumed, but its checkpoint result is unknown; manual reconciliation required"
            state["requires_user_approval"]=True
            state["updated_at"]=autopilot.utc_now()
            autopilot.save_state(state_path,state)
            autopilot.append_event(state_dir/"events.jsonl","approval_consumed_outcome_unknown",state,
                                   {"request_id":request_id,"action":action})
            return {"status":"WAITING_APPROVAL","ran":False,"reason":"approval consumed; outcome unknown; refusing replay"}
        if not ledger.is_approved(request_id, task=task, action=action, scope=scope, now=now):
            return {"status": status, "ran": False, "reason": "approval pending or expired"}
        if action == "codex_service_recovery":
            conditions = approval.get("preconditions", {}) if approval else {}
            if not approvals_can_resume(approval, conditions):
                return {"status": status, "ran": False, "reason": "explicit Codex service restoration attestation required"}
        reservation = ledger.reserve(request_id, task=task, action=action, scope=scope, now=now)
        if reservation is None:
            return {"status": status, "ran": False, "reason": "single-use approval already consumed or expired"}
        autopilot.transition(state, "READY", blocking_reason="explicit approval recorded; resuming safe local verification")
        run_token=secrets.token_hex(16)
        state["approval_resume_in_flight"]={
            "request_id":request_id,"action":action,"test_suite":approval.get("test_suite"),
            "run_token":run_token,
        }
        state["approval_resume_recorded_at"] = autopilot.utc_now()
        autopilot.save_state(state_path, state)
        reserved_version=int(state.get("state_version",0))
        autopilot.append_event(state_dir / "events.jsonl", "approved_safe_checkpoint_resumed", state,
                               {"request_id": request_id, "kind": "safe_local_verification"})
        status = "READY"

    if status in {"BLOCKED", "PASSED"}:
        return {"status": status, "ran": False, "reason": "terminal gate remains closed"}
    if status not in {"READY", "RUNNING", "VERIFYING"}:
        return {"status": status or "INVALID", "ran": False, "reason": "unknown state"}

    run = runner or autopilot.run_cycle
    if runner is None:
        capability=autopilot._issue_resume_capability(state_dir,request_id) if reservation else None
        try:
            exit_code = run(state_dir, resume_capability=capability,_held_lock=lock)
        except Exception:
            autopilot._revoke_resume_capability(capability)
            raise
    else:
        exit_code = run(state_dir)
    updated = autopilot.load_state(state_path) or {}
    verification_succeeded=False
    if reservation:
        verification_succeeded=_valid_scoped_verification(updated,request_id=request_id,action=action,
            test_suite=approval.get("test_suite"),run_token=run_token,
            reserved_version=reserved_version,exit_code=exit_code)
        ledger.finish(request_id, outcome="succeeded" if verification_succeeded else "failed", now=now)
        marker=updated.get("approval_resume_in_flight")
        if isinstance(marker,dict) and marker.get("run_token")==run_token:
            updated.pop("approval_resume_in_flight",None)
            if not verification_succeeded and updated.get("status") in {"READY","RUNNING","VERIFYING"}:
                autopilot.transition(updated,"WAITING_APPROVAL",
                    blocking_reason="授权复测结果未能证明指定套件已有效通过；需要人工检查并重新授权",
                    requires_user_approval=True)
            if not verification_succeeded:
                updated["blocking_reason"]="授权复测结果未能证明指定套件已有效通过；需要人工检查并重新授权"
                updated["requires_user_approval"]=True
            autopilot.save_state(state_path, updated)
    return {"status": updated.get("status", "UNKNOWN"), "ran": True, "exit_code": exit_code,
            "verification_succeeded":verification_succeeded}


def serve(state_dir: Path, *, interval_seconds=900, stop_event=None, runner=None, max_ticks=None):
    if not 60 <= int(interval_seconds) <= 24 * 3600:
        raise ValueError("scheduler interval must be between 60 seconds and 24 hours")
    ticks = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return ticks
        if (Path(state_dir) / "STOP").exists():
            return ticks
        tick(Path(state_dir), runner=runner)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return ticks
        end = time.monotonic() + int(interval_seconds)
        while time.monotonic() < end:
            if stop_event is not None and stop_event.wait(min(1, end - time.monotonic())):
                return ticks
            if (Path(state_dir) / "STOP").exists() or (Path(state_dir) / "PAUSE").exists():
                if stop_event is not None:
                    stop_event.wait(1)
                else:
                    time.sleep(1)
                if (Path(state_dir) / "STOP").exists():
                    return ticks
                if not (Path(state_dir) / "PAUSE").exists():
                    break
