from __future__ import annotations
from pathlib import Path
try:
    from . import autopilot, notifications
except ImportError:
    import autopilot, notifications


def notify_blocker(state_dir: Path, *, task, category, blocker, attempt_summary,
                   user_action, resume, credential_store=None, sender=None, now=None):
    state_dir = Path(state_dir)
    store = credential_store or notifications.WindowsCredentialStore()
    try:
        credentials = store.read()
    except Exception:
        credentials = None
        store_error = "credential_store_unavailable"
    else:
        store_error = None
    incident = notifications.incident_id_for(task=task, category=category, blocker=blocker)
    message = notifications.format_alert(
        category=category, task=task, attempt_summary=attempt_summary,
        user_action=user_action, resume=resume,
    )
    ledger = notifications.NotificationLedger(state_dir / "notifications.json")
    sender_fn = sender or notifications.send_alert
    result = notifications.dispatch_alert(ledger, incident, credentials, message,
                                          sender=sender_fn, now=now)
    if store_error and result["error_category"] == "notification_not_configured":
        result["error_category"] = store_error
    state = autopilot.load_state(state_dir / "state.json") or autopilot.new_state("unknown")
    autopilot.append_event(state_dir / "events.jsonl", "human_blocker_notification", state, {
        "incident_id": incident, "category": category, "sent": result["sent"],
        "error_category": result["error_category"],
    })
    return result
