from __future__ import annotations
import hashlib
from pathlib import Path
try:
    from . import autopilot
    from .approvals import ACTIONS, ApprovalError, ApprovalLedger
except ImportError:
    import autopilot
    from approvals import ACTIONS, ApprovalError, ApprovalLedger


class OrchestrationError(RuntimeError):
    pass


def queue_approval(state_dir: Path, *, task: str, action: str, scope: str,
                   resume_kind: str | None = None, expires_minutes=15, now=None, _held_lock=None):
    state_dir=Path(state_dir)
    lock_path=(state_dir/"instance.lock").resolve()
    if _held_lock is not None:
        if not _held_lock.is_held or _held_lock.path.resolve()!=lock_path:
            raise OrchestrationError("approval writer does not hold the matching checkpoint lock")
        return _queue_approval_locked(state_dir,task=task,action=action,scope=scope,
            resume_kind=resume_kind,expires_minutes=expires_minutes,now=now,_held_lock=_held_lock)
    lock=autopilot.SingleInstanceLock(lock_path)
    try:
        lock.acquire()
    except autopilot.InstanceAlreadyRunning as exc:
        raise OrchestrationError("another Autopilot operation owns the checkpoint; approval was not written") from exc
    try:
        return _queue_approval_locked(state_dir,task=task,action=action,scope=scope,
            resume_kind=resume_kind,expires_minutes=expires_minutes,now=now,_held_lock=lock)
    finally:
        lock.release()


def _queue_approval_locked(state_dir: Path, *, task: str, action: str, scope: str,
                           resume_kind: str | None = None, expires_minutes=15, now=None,_held_lock=None):
    state_dir = Path(state_dir)
    if (not _held_lock.is_held or _held_lock.path.resolve()!=(state_dir/"instance.lock").resolve()):
        raise OrchestrationError("approval mutation requires the checkpoint lock")
    if (state_dir/"STOP").exists():
        raise OrchestrationError("emergency STOP marker blocks approval creation")
    state_path = state_dir / "state.json"
    state = autopilot.load_state(state_path)
    if not state or state.get("task_id") != task:
        raise OrchestrationError("approval task does not match the active checkpoint")
    if action not in ACTIONS or not scope or len(scope) > 500:
        raise OrchestrationError("approval action or scope is invalid")
    if resume_kind not in {None, "safe_local_verification"}:
        raise OrchestrationError("no automatic handler exists for this action")
    ledger = ApprovalLedger(state_dir / "approvals.json")
    ledger.expire_pending(now=now)
    current_status=state.get("status")
    existing_id=state.get("approval_request_id")
    if current_status=="WAITING_APPROVAL":
        if action!="repair_resume" or not existing_id:
            raise OrchestrationError("waiting approval cannot be replaced")
        previous=ledger.get(existing_id)
        if not previous or any(previous.get(key)!=value for key,value in
                               (("task",task),("action",action),("scope",scope))):
            raise OrchestrationError("renewal must retain the exact task, action, and scope")
        if previous.get("status") not in {"PENDING","EXPIRED","FAILED","CANCELLED"}:
            raise OrchestrationError("approval outcome is not safe to renew")
    elif current_status not in {"READY", "RUNNING", "VERIFYING", "BLOCKED"}:
        raise OrchestrationError("current state cannot enter approval wait")
    if action=="repair_resume" and resume_kind!="safe_local_verification":
        raise OrchestrationError("repair resume is limited to safe local verification")
    verification_suite=None
    if action in {"repair_resume","codex_service_recovery"}:
        if resume_kind!="safe_local_verification":
            raise OrchestrationError("recovery approval must be limited to local verification")
        verification_suite=autopilot.approved_local_suite(action,scope)
        if not verification_suite:
            raise OrchestrationError("recovery scope must name exactly one allow-listed local test suite")
    elif resume_kind is not None:
        raise OrchestrationError("this approval action has no automatic resume handler")
    request = ledger.find_pending(task=task, action=action, scope=scope,test_suite=verification_suite)
    if request is None:
        request = ledger.create_request(task=task, action=action, scope=scope,
                                        test_suite=verification_suite,expires_minutes=expires_minutes,now=now)
    if current_status!="WAITING_APPROVAL":
        autopilot.transition(state, "WAITING_APPROVAL",
                             blocking_reason="waiting for an explicit, scoped local user approval",
                             requires_user_approval=True)
    else:
        state["blocking_reason"]="waiting for an explicit, scoped local user approval"
        state["requires_user_approval"]=True
        state["updated_at"]=autopilot.utc_now()
    state["approved_resume"] = None
    if resume_kind == "safe_local_verification":
        state["approved_resume"] = {
            "kind": resume_kind, "request_id": request["request_id"],
            "action": action, "scope": scope,
        }
        state["approved_resume"]["suite"]=verification_suite
        state["approved_resume"]["test_suite"]=verification_suite
    state["approval_request_id"] = request["request_id"]
    state["next_action"] = "在本机审阅精确范围并批准或拒绝；批准后计划任务只恢复允许的安全步骤"
    autopilot.save_state(state_path, state)
    scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    autopilot.append_event(state_dir / "events.jsonl", "approval_requested", state, {
        "request_id": request["request_id"], "action": action, "scope_sha256": scope_hash,
        "resume_kind": resume_kind,
    })
    try:
        from . import runtime
    except ImportError:
        import runtime
    category, action_text = {
        "github_authentication": ("github_auth", "使用 Windows Git Credential Manager 完成登录，然后等待权限只读检查。"),
        "provider_request": ("provider_approval", "先确认本机密钥和预算状态，再在本地授权入口批准这一次请求。"),
        "security_review": ("security_review", "查看本机保存的审查材料并完成指定人工安全审查。"),
        "business_decision": ("business_decision", "在本机记录这项明确的业务选择。"),
        "schedule_enable": ("external_dependency", "先确认验收和飞书通知测试均完成，再由本机控制入口启用计划任务。"),
        "deployment": ("deployment_approval", "在本机部署入口明确批准此次指定环境和版本。"),
        "codex_service_recovery": ("external_dependency", "恢复当前 Windows 用户的 Codex CLI 服务连接后，在本机授权入口批准一次安全的本地重试。"),
        "repair_resume": ("repair_limit", "先查看最后失败原因并处理问题，再在本机人工授权入口批准一次安全的本地复测。"),
    }[action]
    runtime.notify_blocker(
        state_dir, task=task, category=category,
        blocker=f"{action}:{scope_hash}",
        attempt_summary="已保存限时、单次、限定范围的请求；尚未执行任何外部操作。",
        user_action=action_text,
        resume="完成上述操作后，Autopilot 会验证检查点；只有明确批准且安全前置条件通过才会恢复。",
    )
    return request
