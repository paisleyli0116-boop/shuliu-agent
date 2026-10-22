from __future__ import annotations
import json
import os
import re
import secrets
import datetime as dt
from pathlib import Path
try:
    from . import task_plan
except ImportError:
    import task_plan

STATUS={"NOT_STARTED","READY","RUNNING","VERIFYING","BLOCKED","WAITING_APPROVAL","PASSED"}
FULL_SHA=re.compile(r"^[0-9a-f]{40}$")
HASH64=re.compile(r"^[0-9a-f]{64}$")
GITHUB_RUN=re.compile(r"^https://github\.com/[^/]+/[^/]+/actions/runs/[0-9]+$")
BASELINE_COMPLETED=tuple(range(1,27))
TRUSTED_REPOSITORY="paisleyli0116-boop/shuliu-agent"
WINDOWS_WORKFLOW="Windows Validation"
GATE_EVIDENCE_POLICY={
    (28,"real_model_provider_connection"):("provider","desktop_secretref_gateway_canary"),
    (28,"model_provider_license_security"):("manual_review","human_provider_review"),
    (29,"model_usage_budget_persistence"):("local_test","postgres_integration"),
    (30,"real_embedding_knowledge_retrieval"):("provider","real_embedding_knowledge_integration"),
    (31,"knowledge_document_ingestion_pdf_docx_xlsx"):("local_test","document_ingestion_integration"),
    (32,"knowledge_server_rbac_trace_audit"):("local_test","knowledge_server_integration"),
    (33,"durable_idempotency"):("local_test","durable_idempotency_integration"),
    (34,"process_crash_recovery"):("process_integration","windows_process_recovery_integration"),
    (35,"skill_security_scan"):("security_scan","approved_scanner_run"),
    (36,"skill_sandbox_provider"):("sandbox_attack_test","windows_sandbox_attack_runner"),
    (27,"all_required_gates_from_release_manifest"):("release_manifest","release_gate_aggregator"),
}


class ProgressError(ValueError):
    pass


def initial_progress(catalog, *, last_commit_sha, baseline_completed=BASELINE_COMPLETED):
    if not FULL_SHA.fullmatch(str(last_commit_sha).lower()):
        raise ProgressError("full commit SHA is required")
    done=set(baseline_completed)
    tasks={}
    for number in catalog.task_order:
        tasks[str(number)]={"status":"BLOCKED" if number in {27,28} else "NOT_STARTED",
                            "commit_sha":last_commit_sha,"gate_evidence":{},
                            "test_results":[],"repair_count":0,"blocking_reason":None}
    return {"schema_version":1,"branch":"feat/foundation-v0.1","last_commit_sha":last_commit_sha,
            "baseline_completed":sorted(done),"plan_sha256":catalog.source_sha256,
            "task_order":list(catalog.task_order),"current_task":28,"tasks":tasks}


def next_task(data):
    tasks=data.get("tasks",{})
    for number in data.get("task_order",()):
        if tasks.get(str(number),{}).get("status")!="PASSED":
            return number
    return None


def _safe_reference(value):
    if (not isinstance(value,str) or not value or len(value)>500 or "://" in value
            or "\\" in value or ":" in value or value.startswith("~")):
        return False
    parts=value.split("/")
    return (len(parts)>=3 and parts[:2]==["docs","evidence"]
            and not any(part in {"",".","..",".git"} for part in parts))


def _timestamp(value):
    if not isinstance(value,str):
        return None
    try:
        parsed=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _validity(record, *, max_days):
    verified=_timestamp(record.get("verified_at"))
    expires=_timestamp(record.get("expires_at"))
    now=dt.datetime.now(dt.timezone.utc)
    return bool(verified and expires and verified<=now+dt.timedelta(minutes=5)
                and expires>now and expires>verified
                and expires-verified<=dt.timedelta(days=max_days))


def evidence_policy(task_number,gate):
    if gate=="windows_validation":
        return "github","github_actions_api"
    return GATE_EVIDENCE_POLICY.get((task_number,gate))


def _validate_evidence(task_number, gate, record, target_sha):
    if not isinstance(record,dict) or not FULL_SHA.fullmatch(str(record.get("sha","")).lower()):
        raise ProgressError("gate evidence must be bound to a full commit SHA")
    if record["sha"].lower()!=target_sha.lower():
        raise ProgressError("gate evidence SHA does not match the task commit")
    kind=record.get("type")
    expected=evidence_policy(task_number,gate)
    if expected is None or (kind,record.get("source"))!=expected:
        raise ProgressError("gate evidence type or source does not match the approved gate policy")
    if kind=="local_test":
        if (record.get("exit_code")!=0 or not isinstance(record.get("command"),str) or not record["command"]
                or not _validity(record,max_days=7)):
            raise ProgressError("local test evidence is incomplete")
        required_local_checks={
            (29,"model_usage_budget_persistence"):("migration_applied","restart_recovered","duplicate_settlement_idempotent"),
            (31,"knowledge_document_ingestion_pdf_docx_xlsx"):("license_reviewed","pdf_docx_xlsx_verified"),
            (32,"knowledge_server_rbac_trace_audit"):("rbac_verified","trace_verified","audit_verified"),
            (33,"durable_idempotency"):("duplicate_request_reconciled","side_effect_once"),
        }.get((task_number,gate),())
        if any(record.get(field) is not True for field in required_local_checks):
            raise ProgressError("local integration evidence is missing a required acceptance check")
    elif kind=="github":
        run_url=str(record.get("run_url",""))
        match=GITHUB_RUN.fullmatch(run_url)
        if (record.get("conclusion")!="success" or record.get("target_sha")!=target_sha.lower()
                or record.get("repository")!=TRUSTED_REPOSITORY
                or record.get("workflow")!=WINDOWS_WORKFLOW
                or not match or record.get("run_id")!=match.group(0).rsplit("/",1)[-1]
                or not run_url.startswith(f"https://github.com/{TRUSTED_REPOSITORY}/")
                or not _validity(record,max_days=30)):
            raise ProgressError("GitHub evidence is not a successful run for the exact SHA")
    elif kind=="provider":
        if (record.get("status")!="success" or not record.get("request_id") or not record.get("trace_id")
                or record.get("one_time_request") is not True or not _validity(record,max_days=7)):
            raise ProgressError("Provider gate requires a successful one-time request and Trace ID")
        if task_number==30 and (record.get("database_verified") is not True
                                or record.get("acl_checked_before_vector_search") is not True):
            raise ProgressError("real embedding evidence is missing database or ACL-first retrieval proof")
    elif kind=="manual_review":
        if (record.get("status")!="approved" or record.get("reviewer_confirmed") is not True
                or not _safe_reference(record.get("review_ref")) or not _validity(record,max_days=180)
                or record.get("license_reviewed") is not True
                or record.get("data_processing_reviewed") is not True
                or record.get("security_reviewed") is not True):
            raise ProgressError("human review evidence is not confirmed")
    elif kind=="process_integration":
        if (record.get("status")!="passed" or record.get("forced_termination") is not True
                or record.get("checkpoint_reconciled") is not True
                or record.get("no_duplicate_side_effect") is not True or not _validity(record,max_days=7)):
            raise ProgressError("process recovery evidence is incomplete")
    elif kind=="security_scan":
        if (record.get("status")!="clean" or not record.get("engine_version")
                or not record.get("ruleset_version") or record.get("eicar_blocked") is not True
                or record.get("clean_package_passed") is not True or not _validity(record,max_days=30)):
            raise ProgressError("security scan evidence is incomplete")
    elif kind=="sandbox_attack_test":
        if (record.get("platform")!="windows" or record.get("attacks_verified") is not True
                or record.get("unsupported_controls_blocked") is not True or not _validity(record,max_days=30)):
            raise ProgressError("sandbox attack evidence is incomplete")
    elif kind=="release_manifest":
        if (record.get("status")!="verified" or not HASH64.fullmatch(str(record.get("manifest_sha256","")))
                or not _validity(record,max_days=30) or record.get("all_required_gates_verified") is not True):
            raise ProgressError("release manifest evidence is invalid")
    else:
        raise ProgressError("unsupported gate evidence type")


def required_gates(catalog,number):
    return set(catalog.tasks[number].release_gates)|{"windows_validation"}


def set_gate_evidence(data,catalog,number,gate,evidence):
    entry=catalog.tasks.get(number)
    if not entry or gate not in required_gates(catalog,number):
        raise ProgressError("gate is not an original plan gate or the required Windows CI delivery gate")
    task=data["tasks"][str(number)]
    if task["status"]=="PASSED":
        raise ProgressError("passed task evidence is immutable")
    _validate_evidence(number,gate,evidence,task["commit_sha"])
    task["gate_evidence"][gate]=dict(evidence)


def set_status(data,catalog,number,status,*,gate_evidence=None,blocking_reason=None):
    if status not in STATUS or number not in catalog.tasks:
        raise ProgressError("invalid task or status")
    current=next_task(data)
    if current!=number:
        raise ProgressError("only the next task in the approved order may change")
    entry=catalog.tasks[number]
    task=data["tasks"][str(number)]
    if status in {"READY","RUNNING","VERIFYING"}:
        if not catalog.startable(number,{int(k):v["status"] for k,v in data["tasks"].items()},
                                 baseline_completed=data.get("baseline_completed",())):
            raise ProgressError("task dependencies or previous tasks are not satisfied")
    if gate_evidence:
        for gate,record in gate_evidence.items():
            set_gate_evidence(data,catalog,number,gate,record)
    if status=="PASSED":
        missing=required_gates(catalog,number)-set(task["gate_evidence"])
        if missing:
            raise ProgressError("required original gates are missing: "+", ".join(sorted(missing)))
        for gate,record in task["gate_evidence"].items():
            _validate_evidence(number,gate,record,task["commit_sha"])
    task["status"]=status
    task["blocking_reason"]=blocking_reason
    data["current_task"]=next_task(data)
    return data


def record_commit(data,number,sha):
    if number not in data.get("task_order",()) or not FULL_SHA.fullmatch(str(sha).lower()):
        raise ProgressError("full feature-branch commit SHA is required")
    if next_task(data)!=number:
        raise ProgressError("cannot update a non-current task commit")
    data["tasks"][str(number)]["commit_sha"]=sha.lower()
    data["last_commit_sha"]=sha.lower()


def save_progress(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+"."+secrets.token_hex(8)+".tmp")
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        os.replace(temp,path)
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass


def load_progress(path,catalog):
    path=Path(path)
    try:data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:raise ProgressError("progress file is unreadable") from exc
    if data.get("schema_version")!=1 or data.get("plan_sha256")!=catalog.source_sha256:
        raise ProgressError("progress schema or approved plan hash changed")
    if tuple(data.get("task_order",()))!=catalog.task_order:
        raise ProgressError("progress task order differs from approved plan")
    for key,task in data.get("tasks",{}).items():
        if task.get("status") not in STATUS:raise ProgressError("unknown persisted task status")
        for gate,record in task.get("gate_evidence",{}).items():
            number=int(key)
            try:
                _validate_evidence(number,gate,record,task.get("commit_sha",""))
            except ProgressError:
                if task.get("status")!="PASSED":raise
                task["status"]="BLOCKED"
                task["blocking_reason"]="required gate evidence is invalid, expired, or from an unapproved source"
                break
        if task.get("status")=="PASSED" and required_gates(catalog,int(key))-set(task.get("gate_evidence",{})):
            task["status"]="BLOCKED"
            task["blocking_reason"]="required gate evidence is missing"
    data["current_task"]=next_task(data)
    return data


def ensure_progress(path,catalog,*,last_commit_sha):
    path=Path(path)
    if path.exists():
        data=load_progress(path,catalog)
    else:
        data=initial_progress(catalog,last_commit_sha=last_commit_sha)
    current=next_task(data)
    if current is not None and data["tasks"][str(current)]["commit_sha"]!=last_commit_sha:
        record_commit(data,current,last_commit_sha)
    save_progress(path,data)
    return data


def record_current_status(path,catalog,number,status,*,last_commit_sha,blocking_reason=None):
    data=ensure_progress(path,catalog,last_commit_sha=last_commit_sha)
    if next_task(data)!=number:
        raise ProgressError("checkpoint does not match the next approved task")
    set_status(data,catalog,number,status,blocking_reason=blocking_reason)
    save_progress(path,data)
    return data
