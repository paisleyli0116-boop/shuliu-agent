from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
import threading
from contextlib import contextmanager
from pathlib import Path

ACTIONS={"provider_request","github_authentication","security_review","business_decision","schedule_enable","deployment","codex_service_recovery","repair_resume"}
TERMINAL={"SUCCEEDED","FAILED","OUTCOME_UNKNOWN","EXPIRED","CANCELLED"}
_THREAD_LOCK=threading.RLock()


class ApprovalError(RuntimeError):
    pass


def _now(value=None):
    return value or dt.datetime.now(dt.timezone.utc)


def _parse(value):
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _confirmation_for(action, request_id):
    confirmation="APPROVE ONCE "+request_id
    if action=="codex_service_recovery":
        confirmation+=" CODEX SERVICE RESTORED"
    return confirmation


def can_resume_external_action(request, conditions):
    if not request or request.get("status")!="APPROVED":
        return False
    if request.get("action")=="provider_request":
        return all(conditions.get(key) is True for key in (
            "secret_configured","budget_available","canary_enabled","policy_allowed","request_matches_scope"))
    if request.get("action")=="github_authentication":
        return conditions.get("remote_read_access") is True
    if request.get("action")=="security_review":
        return conditions.get("human_review_recorded") is True
    if request.get("action")=="business_decision":
        return conditions.get("decision_recorded") is True
    if request.get("action")=="schedule_enable":
        return conditions.get("acceptance_tests_passed") is True and conditions.get("human_setup_complete") is True
    if request.get("action")=="codex_service_recovery":
        return (request.get("preconditions",{}).get("codex_service_restored") is True
                and conditions.get("codex_service_restored") is True)
    return False


class ApprovalLedger:
    def __init__(self,path:Path):
        self.path=Path(path)

    def _read(self):
        if not self.path.exists():
            return {"schema_version":1,"requests":{}}
        try:
            data=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc:
            raise ApprovalError("approval ledger is unreadable") from exc
        if data.get("schema_version")!=1 or not isinstance(data.get("requests"),dict):
            raise ApprovalError("unsupported approval ledger")
        return data

    def _write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_name(self.path.name+"."+secrets.token_hex(8)+".tmp")
        try:
            temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            os.replace(temp,self.path)
        finally:
            try:temp.unlink(missing_ok=True)
            except OSError:pass

    @contextmanager
    def _exclusive(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        lock_path=self.path.with_name(self.path.name+".lock")
        fd=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
        acquired=False
        with _THREAD_LOCK:
            try:
                if os.fstat(fd).st_size==0:
                    os.write(fd,b"0")
                deadline=time.monotonic()+10
                while not acquired:
                    os.lseek(fd,0,os.SEEK_SET)
                    try:
                        if os.name=="nt":
                            import msvcrt
                            msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
                        else:
                            import fcntl
                            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        acquired=True
                    except OSError:
                        if time.monotonic()>=deadline:
                            raise ApprovalError("approval ledger lock timed out")
                        time.sleep(0.025)
                yield
            finally:
                if acquired:
                    try:
                        os.lseek(fd,0,os.SEEK_SET)
                        if os.name=="nt":
                            import msvcrt
                            msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
                        else:
                            import fcntl
                            fcntl.flock(fd,fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
                else:
                    os.close(fd)

    def create_request(self,*,task,action,scope,test_suite=None,now=None,expires_minutes=15):
        if action not in ACTIONS or not task or not scope or len(scope)>500:
            raise ApprovalError("invalid approval request")
        if test_suite is not None and (not isinstance(test_suite,str) or not test_suite or len(test_suite)>200):
            raise ApprovalError("invalid approval test suite")
        if not 1<=int(expires_minutes)<=60:
            raise ApprovalError("approval expiry must be between 1 and 60 minutes")
        timestamp=_now(now)
        request_id=secrets.token_hex(12)
        confirmation=_confirmation_for(action,request_id)
        record={
            "request_id":request_id,"task":task,"action":action,"scope":scope,"test_suite":test_suite,
            "max_uses":1,"status":"PENDING","created_at":timestamp.isoformat(),
            "expires_at":(timestamp+dt.timedelta(minutes=int(expires_minutes))).isoformat(),
            "confirmation_hash":_digest(confirmation),"uses":0,
        }
        with self._exclusive():
            data=self._read()
            data["requests"][request_id]=record
            self._write(data)
        return {**record,"confirmation":confirmation}

    def find_pending(self, *, task, action, scope, test_suite=None):
        return next((r for r in self.pending()
                     if r.get("task")==task and r.get("action")==action and r.get("scope")==scope
                     and r.get("test_suite")==test_suite),None)

    def expire_pending(self, *, now=None):
        """Mark elapsed pending approvals terminal before considering a renewal."""
        timestamp=_now(now)
        expired=[]
        with self._exclusive():
            data=self._read()
            for request_id,request in data["requests"].items():
                if request.get("status")!="PENDING":
                    continue
                expires=_parse(request.get("expires_at"))
                if expires is None or timestamp>=expires:
                    request["status"]="EXPIRED"
                    request["expired_at"]=timestamp.isoformat()
                    expired.append(request_id)
            if expired:
                self._write(data)
        return expired

    def get(self,request_id):
        return self._read()["requests"].get(request_id)

    def pending(self):
        data=self._read()
        return [dict(r) for r in data["requests"].values() if r.get("status")=="PENDING"]

    def approve(self,request_id,*,confirmation,now=None):
        with self._exclusive():
            data=self._read();r=data["requests"].get(request_id)
            if not r or r.get("status")!="PENDING":
                return False
            expires=_parse(r.get("expires_at"))
            if expires is None or _now(now)>=expires:
                r["status"]="EXPIRED";self._write(data);return False
            if not secrets.compare_digest(r.get("confirmation_hash",""),_digest(str(confirmation))):
                return False
            r["status"]="APPROVED";r["approved_at"]=_now(now).isoformat()
            if r.get("action")=="codex_service_recovery":
                r["preconditions"]={"codex_service_restored":True}
            self._write(data)
            return True

    def is_approved(self,request_id,*,task=None,action=None,scope=None,now=None):
        r=self.get(request_id)
        if not r or r.get("status")!="APPROVED" or r.get("uses")!=0:
            return False
        if task is not None and r.get("task")!=task:return False
        if action is not None and r.get("action")!=action:return False
        if scope is not None and r.get("scope")!=scope:return False
        expires=_parse(r.get("expires_at"))
        return expires is not None and _now(now)<expires

    def reserve(self,request_id,*,task,action,scope,now=None):
        with self._exclusive():
            data=self._read();r=data["requests"].get(request_id)
            if not r or r.get("status")!="APPROVED" or r.get("uses")!=0:
                return None
            if r.get("task")!=task or r.get("action")!=action or r.get("scope")!=scope:
                return None
            expires=_parse(r.get("expires_at"))
            if expires is None or _now(now)>=expires:
                r["status"]="EXPIRED";self._write(data);return None
            r["status"]="OUTCOME_UNKNOWN"
            r["uses"]=1
            r["reserved_at"]=_now(now).isoformat()
            self._write(data)
            return {"request_id":request_id,"task":task,"action":action,"scope":scope,"reserved_at":r["reserved_at"]}

    def finish(self,request_id,*,outcome,now=None):
        if outcome not in {"succeeded","failed"}:
            raise ApprovalError("unknown outcomes cannot be retried or marked complete")
        with self._exclusive():
            data=self._read();r=data["requests"].get(request_id)
            if not r or r.get("status")!="OUTCOME_UNKNOWN" or r.get("uses")!=1:
                return False
            r["status"]="SUCCEEDED" if outcome=="succeeded" else "FAILED"
            r["finished_at"]=_now(now).isoformat()
            self._write(data)
            return True


def approve_pending_interactive(ledger, *, request_ids=None, is_tty=None, input_fn=input, output_fn=print):
    if (ledger.path.parent/"STOP").exists():
        raise ApprovalError("emergency STOP marker blocks approval changes")
    if is_tty is None:
        is_tty = bool(sys.stdin.isatty() and sys.stdout.isatty())
    if not is_tty:
        raise ApprovalError("approval requires a person at the local interactive console")
    requests = ledger.pending()
    if request_ids is not None:
        allowed = set(request_ids)
        requests = [request for request in requests if request["request_id"] in allowed]
    approved = 0
    for request in requests:
        output_fn("人工授权请求")
        output_fn("任务："+str(request["task"]))
        output_fn("操作："+str(request["action"]))
        output_fn("范围："+str(request["scope"]))
        output_fn("有效期至："+str(request["expires_at"]))
        expected = _confirmation_for(request.get("action"),request["request_id"])
        instruction = "若已恢复 Codex 服务并同意此单次操作，请输入：" if request.get("action")=="codex_service_recovery" else "若同意此单次操作，请输入："
        response = input_fn(instruction+expected+"；拒绝请直接回车：")
        if secrets.compare_digest(str(response).strip(), expected):
            try:
                try:
                    from . import autopilot as operation_gate
                except ImportError:
                    import autopilot as operation_gate
                approved_now=operation_gate._run_before_stop(
                    ledger.path.parent,
                    lambda:ledger.approve(request["request_id"],confirmation=expected))
            except operation_gate.EmergencyStopRequested as exc:
                raise ApprovalError("emergency STOP marker blocks approval changes") from exc
            except operation_gate.OperationGateUnavailable as exc:
                raise ApprovalError("operation gate is unavailable; approval was not changed") from exc
            if approved_now:
                approved += 1
    return approved
