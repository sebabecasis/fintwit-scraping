"""Resumable delivery of a frozen report, independent of collection and scoring.

Persist the bundle and receipt directory on durable storage. An interrupted send
has an uncertain outcome and is NOT automatically resent.
"""
import hashlib
import json
from pathlib import Path


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str))
    temp.replace(path)


def deliver(bundle_path, *, publish=None, send=None, email_to=None):
    path = Path(bundle_path)
    bundle = json.loads(path.read_text())
    fingerprint = hashlib.sha256(json.dumps(bundle, sort_keys=True).encode()).hexdigest()
    state_path = path.with_suffix(".delivery.json")
    lock = path.with_suffix(".delivery.lock")
    with lock.open("x"):
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {"bundle_hash": fingerprint}
            if state["bundle_hash"] != fingerprint:
                raise ValueError("Report changed since delivery; use a new bundle path")
            if send and not email_to:
                raise ValueError("Email destination is required")
            if send and state.get("email_to", email_to) != email_to:
                raise ValueError("Email recipient differs from original delivery")
            if send:
                state["email_to"] = email_to
            for name, action in (("publish", publish), ("email", send)):
                if action is None or state.get(name, {}).get("status") == "success":
                    continue
                if state.get(name, {}).get("status") in {"in_flight", "uncertain"}:
                    raise RuntimeError(f"{name} outcome uncertain; reconcile provider receipt before retry")
                state[name] = {"status": "in_flight"}
                save(state_path, state)
                try:
                    result = action(bundle, state.get("publish", {}).get("receipt"))
                    if not result:
                        raise RuntimeError("Provider returned no receipt")
                    state[name] = {"status": "success", "receipt": result}
                except Exception as exc:
                    state[name] = {"status": "uncertain", "error": type(exc).__name__}
                    save(state_path, state)
                    raise
                save(state_path, state)
            return state
        finally:
            lock.unlink()


def deliver_live(path, *, no_publish=False, no_email=False):
    # Import network clients only for explicitly enabled stages.
    import os
    publish = send = None
    if not no_publish:
        from .gist import publish_dashboard
        def publish(bundle, _):
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                html = Path(directory) / "index.html"
                html.write_text(bundle["html_content"])
                return publish_dashboard(bundle["week_start"], html)
    if not no_email:
        from .email import send_weekly_email
        send = lambda bundle, url: send_weekly_email(bundle["week_start"], bundle["week_end"], bundle["metrics"],
            new_following=bundle["new_following"], narrative=bundle["narrative"],
            new_ticker_summaries=bundle["new_ticker_data"], dashboard_url=url)
    return deliver(path, publish=publish, send=send, email_to=os.getenv("EMAIL_TO"))
