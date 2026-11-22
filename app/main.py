"""Loan desk API.

    GET  /health                              no key; for the load balancer / your browser
    GET  /queue                               applications waiting in data/applications.csv
    POST /applications                        {"text": "..."} or {"application_id": "LA-26001"}  -> runs until officer review
    GET  /applications/{id}                   current state
    POST /applications/{id}/decision          {"decision": "approve" | "reject", "note": "...", "conditions": "..."}

Every call except /health needs the header X-API-Key with an officer key from OFFICER_API_KEYS.
Run locally:  uvicorn app.main:app --reload --port 8000   then open http://127.0.0.1:8000/docs
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from app import config, data, pipeline

config.check()
config.LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loan_desk")

app = FastAPI(title="Sahyadri Bank Loan Desk", version="1.0",
              description="Multi-agent loan application processing with officer approval.")


def audit(officer: str, action: str, application_id: str, **details):
    """Append-only audit trail: who did what, when. One JSON object per line."""
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "officer": officer,
             "action": action, "application_id": application_id, **details}
    with open(config.LOG_DIR / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def current_officer(x_api_key: str | None = Header(default=None)) -> str:
    name = config.OFFICERS.get(x_api_key or "")
    if not name:
        raise HTTPException(status_code=401, detail="Missing or unknown X-API-Key")
    return name


class NewApplication(BaseModel):
    text: str | None = Field(None, max_length=4000, description="The customer's message")
    application_id: str | None = Field(None, description="Or the id of an application waiting in the queue")

    @model_validator(mode="after")
    def one_of(self):
        if not (self.text or self.application_id):
            raise ValueError("send either text or application_id")
        return self


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field("", max_length=500)
    conditions: str = Field("", max_length=300, description="Optional sanction conditions, e.g. 'complete re-KYC'")


def summary(application_id: str, s: dict) -> dict:
    """What the officer sees: the facts, never the raw state."""
    out = {"application_id": application_id, "status": s.get("status")}
    if s.get("status") == "needs_info":
        out["missing"] = s.get("missing")
        out["extracted"] = s.get("request")
        return out
    if s.get("status") == "unknown_applicant":
        out["detail"] = f"No customer with id {s['request']['applicant_id']}"
        return out
    p, e = s.get("profile", {}), s.get("eligibility", {})
    out["applicant"] = {k: p.get(k) for k in ["applicant_id", "name", "age", "city", "employment_type", "employer",
                                              "monthly_income", "credit_score", "kyc_status"]}
    out["request"] = s.get("request")
    out["numbers"] = {k: e.get(k) for k in ["rate_percent", "emi", "foir", "foir_limit", "total_payable"]}
    out["failed_rules"] = e.get("failed_rules")
    out["risk_flags"] = e.get("risk_flags")
    out["risk_review"] = s.get("risk")
    out["recommendation"] = s.get("recommendation")
    if s.get("final"):
        out["final"] = s["final"]
        out["officer"] = s.get("officer")
        out["letter"] = s.get("letter")
    return out


@app.get("/health")
def health():
    return {"status": "ok", "model": config.MODEL, "policy_clauses": len(pipeline.agents.policies.CHUNKS)}


@app.get("/queue")
def queue(officer: str = Depends(current_officer)):
    q = data.applications.reset_index()
    return [{"application_id": r.application_id, "received_on": r.received_on, "channel": r.channel,
             "preview": r.application_text[:80]} for r in q.itertuples()]


@app.post("/applications")
def new_application(body: NewApplication, officer: str = Depends(current_officer)):
    if body.application_id:
        text = data.get_application_text(body.application_id)
        if text is None:
            raise HTTPException(404, f"No application {body.application_id} in the queue")
        app_id = body.application_id
    else:
        text, app_id = body.text, f"LA-{uuid.uuid4().hex[:8].upper()}"
    if pipeline.state_of(app_id):
        raise HTTPException(409, f"{app_id} was already processed; GET /applications/{app_id}")
    t0 = time.perf_counter()
    try:
        s = pipeline.start(app_id, text)
    except Exception:
        log.exception("pipeline failed for %s", app_id)
        raise HTTPException(502, "The assessment could not be completed (LLM or data error). Try again.")
    secs = round(time.perf_counter() - t0, 2)
    audit(officer, "assessed", app_id, status=s.get("status"),
          recommendation=(s.get("recommendation") or {}).get("decision"), seconds=secs)
    log.info("assessed %s status=%s in %.2fs", app_id, s.get("status"), secs)
    return summary(app_id, s)


@app.get("/applications/{application_id}")
def get_application(application_id: str, officer: str = Depends(current_officer)):
    s = pipeline.state_of(application_id)
    if not s:
        raise HTTPException(404, f"No processed application {application_id}")
    return summary(application_id, s)


@app.post("/applications/{application_id}/decision")
def decide(application_id: str, body: Decision, officer: str = Depends(current_officer)):
    s = pipeline.state_of(application_id)
    if not s:
        raise HTTPException(404, f"No processed application {application_id}")
    if s.get("status") != "awaiting_officer":
        raise HTTPException(409, f"{application_id} is not waiting for a decision (status: {s.get('status')})")
    try:
        s = pipeline.decide(application_id, body.decision, f"{body.note} (by {officer})".strip(), body.conditions)
    except Exception:
        log.exception("decision failed for %s", application_id)
        raise HTTPException(502, "Could not complete the decision (letter writer failed). Try again.")
    audit(officer, "decided", application_id, officer_decision=body.decision,
          final=s["final"]["decision"], note=body.note)
    return summary(application_id, s)
