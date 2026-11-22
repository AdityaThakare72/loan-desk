"""The loan pipeline as a LangGraph graph.

    intake (LLM) -> load_profile (code) -> check_rules (code) -> review_risk (LLM) -> recommend (code)
        -> officer_review (human, interrupt) -> write_letter (LLM)

If a hard rule already failed, review_risk is skipped: the decision is made, an LLM call would only add cost.

Early exits: a message without the needed details, or an unknown customer id, stops after intake / load_profile.
The graph pauses at officer_review and continues when the officer's decision arrives (Session 7: interrupt + Command).
"""
import re
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import agents, rules
from app.data import get_profile


class LoanState(TypedDict, total=False):
    text: str
    request: dict
    missing: list
    profile: dict
    eligibility: dict
    risk: dict
    recommendation: dict
    officer: dict
    final: dict
    letter: str
    status: str


REQUIRED = ["applicant_id", "product", "amount_inr", "tenure_months", "purpose"]


def intake(state: LoanState):
    request = agents.intake(state["text"])
    missing = [f for f in REQUIRED if not request.get(f)]
    if request.get("applicant_id") and not re.fullmatch(r"A\d{4}", request["applicant_id"]):
        missing.append("applicant_id (format A1234)")
    if missing:
        return {"request": request, "missing": missing, "status": "needs_info"}
    return {"request": request, "missing": []}


def load_profile(state: LoanState):
    profile = get_profile(state["request"]["applicant_id"])
    if profile is None:
        return {"status": "unknown_applicant"}
    return {"profile": profile}


def check_rules(state: LoanState):
    return {"eligibility": rules.check_eligibility(state["request"], state["profile"])}


def review_risk(state: LoanState):
    return {"risk": agents.review_risk(state["request"], state["profile"], state["eligibility"])}


def recommend(state: LoanState):
    risk = state.get("risk") or {}       # empty when review_risk was skipped
    return {"recommendation": rules.recommend(state["eligibility"], risk), "status": "awaiting_officer"}


def officer_review(state: LoanState):
    # pause here; the API returns this summary to the officer and waits for /decision
    answer = interrupt({"recommendation": state["recommendation"]["decision"],
                        "reasons": state["recommendation"]["reasons"]})
    rec = state["recommendation"]
    decision = answer["decision"].upper()
    # Lending Policy section 1: a hard rule cannot be overridden. Enforced here in code, whatever the officer sends.
    if decision == "APPROVE" and rec["hard_rule_failed"]:
        decision = "REJECT"
        answer = {**answer, "note": (answer.get("note", "") + " [override blocked: hard rule failed]").strip()}
    final = {"decision": decision, "reasons": rec["reasons"], "conditions": answer.get("conditions", "")}
    if decision == "REJECT" and rec["decision"] == "APPROVE":
        # the officer declined a clean application: the letter cites the officer's authority, not internal notes
        final["reasons"] = ["Declined after review by the loan officer (LP-12)"]
    return {"officer": answer, "final": final}


def write_letter(state: LoanState):
    letter = agents.write_letter(state["profile"]["name"], state["request"], state["eligibility"], state["final"])
    return {"letter": letter, "status": "approved" if state["final"]["decision"] == "APPROVE" else "declined"}


def after_intake(state: LoanState):
    return END if state.get("status") == "needs_info" else "load_profile"


def after_profile(state: LoanState):
    return END if state.get("status") == "unknown_applicant" else "check_rules"


def after_rules(state: LoanState):
    return "recommend" if state["eligibility"]["failed_rules"] else "review_risk"


def build_graph(checkpointer=None):
    g = StateGraph(LoanState)
    for name, fn in [("intake", intake), ("load_profile", load_profile), ("check_rules", check_rules),
                     ("review_risk", review_risk), ("recommend", recommend),
                     ("officer_review", officer_review), ("write_letter", write_letter)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", after_intake, ["load_profile", END])
    g.add_conditional_edges("load_profile", after_profile, ["check_rules", END])
    g.add_conditional_edges("check_rules", after_rules, ["review_risk", "recommend"])
    g.add_edge("review_risk", "recommend")
    g.add_edge("recommend", "officer_review")
    g.add_edge("officer_review", "write_letter")
    g.add_edge("write_letter", END)
    # InMemorySaver: paused applications live in this process's memory. Lost on restart: see the README.
    return g.compile(checkpointer=checkpointer or InMemorySaver())


graph = build_graph()


def start(application_id: str, text: str) -> dict:
    cfg = {"configurable": {"thread_id": application_id}}
    graph.invoke({"text": text}, cfg)
    return graph.get_state(cfg).values


def decide(application_id: str, decision: str, note: str = "", conditions: str = "") -> dict:
    cfg = {"configurable": {"thread_id": application_id}}
    graph.invoke(Command(resume={"decision": decision, "note": note, "conditions": conditions}), cfg)
    return graph.get_state(cfg).values


def state_of(application_id: str) -> dict | None:
    snap = graph.get_state({"configurable": {"thread_id": application_id}})
    return snap.values or None
