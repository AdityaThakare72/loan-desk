"""The three LLM agents of the pipeline. Each has ONE job, its own prompt and its own output format.

    Intake agent         free-text application  -> LoanRequest (structured)
    Risk reviewer        profile + numbers + policy clauses -> RiskReview (structured)
    Letter writer        final decision + numbers -> decision letter (text)

What the LLM does NOT do: arithmetic, rule checks, the final decision. Those are in rules.py (code) and with the officer.
"""
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app import config, policies
from app.rules import inr


def get_llm(temperature: float = 0):
    return ChatOpenAI(model=config.MODEL, api_key=config.LLM_API_KEY, base_url=config.BASE_URL,
                      temperature=temperature, timeout=config.LLM_TIMEOUT_SECONDS, max_retries=2)


# ------------------------------------------------------------------ 1. intake agent
class LoanRequest(BaseModel):
    """A loan request extracted from a customer's message."""
    applicant_id: str | None = Field(None, description="Customer id exactly as written, like A1023. null if not given.")
    product: Literal["personal", "car", "home"] | None = Field(None, description="Loan product. null if unclear.")
    amount_inr: float | None = Field(None, description="Amount in rupees as a plain number. 1 lakh = 100000, 1 crore = 10000000.")
    tenure_months: int | None = Field(None, description="Repayment period in months (years x 12). null if not given.")
    purpose: str | None = Field(None, description="What the money is for, in the customer's words. null if not given.")


INTAKE_PROMPT = """You extract loan applications for Sahyadri Digital Bank.
Read the customer's message and fill the fields. Copy only what the message says: never guess a missing value, use null.
Convert amounts to rupees (8 lakh = 800000, 1.2 crore = 12000000) and tenure to months (5 years = 60)."""


def intake(text: str) -> dict:
    llm = get_llm().with_structured_output(LoanRequest, method="function_calling")
    result = llm.invoke([("system", INTAKE_PROMPT), ("user", text)])
    return result.model_dump()


# ------------------------------------------------------------------ 2. risk reviewer
class RiskReview(BaseModel):
    """Assessment of the stated purpose and the risk flags, based only on the policy clauses provided."""
    purpose_summary: str = Field(description="The stated purpose in 3 to 8 words.")
    speculative_purpose: bool = Field(description="True only if the purpose is speculative as defined in LP-11 (shares, F&O, crypto, betting...).")
    unclear_purpose: bool = Field(description="True if the purpose is vague or does not fit the product (RG-6).")
    risk_level: Literal["Low", "Medium", "High"] = Field(description="Per the RG table: no flags Low, one Medium, two or more High.")
    concerns: list[str] = Field(description="Short concerns for the officer, each naming its clause id.")
    questions_for_applicant: list[str] = Field(description="Follow-up questions the officer should ask, from the RG clauses. Can be empty.")


RISK_PROMPT = """You are a credit risk reviewer at Sahyadri Digital Bank.
Use ONLY the policy clauses given. The numbers and rule checks were computed by the bank's system: do not recalculate
or contradict them. Your job: judge the stated purpose against LP-11 and RG-6, set the risk level using the RG table
(count the risk flags given, plus RG-6 if you set unclear_purpose), and write short concerns and follow-up questions,
each naming its clause id."""


def review_risk(request: dict, profile: dict, eligibility: dict) -> dict:
    flag_ids = [f["clause"] for f in eligibility["risk_flags"]]
    found = policies.search(f"purpose {request['purpose']} {request['product']} loan speculative unclear", k=3)
    context = policies.clauses(["LP-11", "RG-6", "RG-7"] + flag_ids) + found
    unique = list({c["clause"]: c for c in context}.values())

    facts = (f"Product: {request['product']} loan, amount {inr(request['amount_inr'])}, {request['tenure_months']} months.\n"
             f"Stated purpose: {request['purpose']}\n"
             f"Applicant: {profile['employment_type']}, {profile['years_in_job']} years with {profile['employer']}, "
             f"income {inr(profile['monthly_income'])}/month, credit score {profile['credit_score']}.\n"
             f"System checks: FOIR {eligibility['foir']:.1%} (limit {eligibility['foir_limit']:.0%}); "
             f"hard rules failed: {[f['clause'] for f in eligibility['failed_rules']] or 'none'}; "
             f"risk flags: {[f['clause'] + ' ' + f['reason'] for f in eligibility['risk_flags']] or 'none'}; "
             f"KYC verified: {eligibility['kyc_ok']}.")
    llm = get_llm().with_structured_output(RiskReview, method="function_calling")
    result = llm.invoke([("system", RISK_PROMPT),
                         ("user", f"POLICY CLAUSES:\n{policies.as_context(unique)}\n\nAPPLICATION:\n{facts}")])
    out = result.model_dump()
    out["clauses_used"] = [c["clause"] for c in unique]
    return out


# ------------------------------------------------------------------ 3. letter writer
LETTER_PROMPT = """You write formal, polite decision letters for Sahyadri Digital Bank to loan applicants.
Rules (LP-13): use exactly the numbers given, never change or round them differently; for a sanction state amount,
tenure, rate and EMI; for a decline state the clauses not met in plain words; promise nothing else.
No internal risk notes, no bureau details beyond what is given. Under 180 words. Plain text, no markdown.
Sign as: Retail Lending Team, Sahyadri Digital Bank."""


def write_letter(name: str, request: dict, eligibility: dict, final: dict) -> str:
    if final["decision"] == "APPROVE":
        facts = (f"Decision: SANCTIONED. Applicant: {name}. Product: {request['product']} loan. "
                 f"Amount: {inr(request['amount_inr'])}. Tenure: {request['tenure_months']} months. "
                 f"Rate: {eligibility['rate_percent']}% p.a. fixed. EMI: {inr(eligibility['emi'], paise=True)}. "
                 f"Conditions: {final.get('conditions') or 'none'}.")
    else:
        facts = (f"Decision: DECLINED. Applicant: {name}. Product: {request['product']} loan of {inr(request['amount_inr'])}. "
                 f"Reasons: {'; '.join(final['reasons'])}.")
    return get_llm(temperature=0.2).invoke([("system", LETTER_PROMPT), ("user", facts)]).content
