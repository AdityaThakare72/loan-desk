"""The Retail Lending Policy and Risk Guidelines as code.

Numbers and hard rules are checked here, in plain Python, never by the LLM:
the same input always gives the same answer, it is testable, and every result names its clause.
Clause ids (LP-3, RG-1, KYC-2) match the policy PDFs.
"""
MIN_SCORE = {"personal": 700, "car": 680, "home": 650}                     # LP-3
MIN_INCOME = {"personal": 25000, "car": 30000, "home": 40000}              # LP-5
TENURE = {"personal": (12, 60), "car": (12, 84), "home": (60, 300)}         # LP-7
RATES = {"personal": (11.5, 13.0), "car": (9.25, 10.0), "home": (8.5, 9.0)}  # LP-2: (score >= 750, below 750)


def inr(x: float, paise: bool = False) -> str:
    """Indian digit grouping: 1250000 -> 'Rs 12,50,000'."""
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail if tail else head[-2:]
        head = head[:-2]
    whole = (head + "," + tail) if head else tail
    return f"Rs {whole}.{frac}" if paise else f"Rs {whole}"


def emi(principal: float, annual_rate: float, months: int) -> float:
    """Reducing-balance EMI (LP-2)."""
    r = annual_rate / 12 / 100
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def max_amount(product: str, income: float) -> float:
    """LP-8."""
    if product == "personal":
        return min(20 * income, 1_500_000)
    if product == "car":
        return 2_500_000
    return min(60 * income, 10_000_000)


def check_eligibility(request: dict, p: dict) -> dict:
    """Hard rules (LP-3 to LP-9) and risk flags (RG-1 to RG-5) for one request and one applicant profile."""
    product, amount, months = request["product"], float(request["amount_inr"]), int(request["tenure_months"])
    income = float(p["monthly_income"])
    salaried = p["employment_type"] == "salaried"

    rate = RATES[product][0] if p["credit_score"] >= 750 else RATES[product][1]
    new_emi = emi(amount, rate, months) if months > 0 else 0.0
    foir = (p["existing_emi"] + new_emi) / income
    foir_limit = 0.50 if salaried else 0.45
    age_limit = 60 if salaried else 65
    age_at_end = p["age"] + months / 12

    failed = []   # (clause, reason)
    if p["credit_score"] < MIN_SCORE[product]:
        failed.append(("LP-3", f"credit score {p['credit_score']} is below the minimum {MIN_SCORE[product]} for a {product} loan"))
    if p["defaults_24m"] > 0:
        failed.append(("LP-4", f"{p['defaults_24m']} loan default(s) in the last 24 months"))
    if income < MIN_INCOME[product]:
        failed.append(("LP-5", f"monthly income {inr(income)} is below the minimum {inr(MIN_INCOME[product])}"))
    if p["age"] < 21 or age_at_end > age_limit:
        failed.append(("LP-6", f"age at the end of the loan would be {age_at_end:.1f}, above the limit of {age_limit}"))
    lo, hi = TENURE[product]
    if not lo <= months <= hi:
        failed.append(("LP-7", f"tenure {months} months is outside {lo} to {hi} months for a {product} loan"))
    if amount > max_amount(product, income):
        failed.append(("LP-8", f"amount {inr(amount)} is above the maximum {inr(max_amount(product, income))}"))
    if foir > foir_limit:
        failed.append(("LP-9", f"FOIR {foir:.2%} is above the limit of {foir_limit:.0%}"))

    flags = []
    if p["enquiries_6m"] >= 4:
        flags.append(("RG-1", f"{p['enquiries_6m']} credit enquiries in 6 months"))
    if p["bounced_6m"] >= 2:
        flags.append(("RG-2", f"{p['bounced_6m']} bounced payments in 6 months"))
    if p["years_in_job"] < (1 if salaried else 2):
        flags.append(("RG-3", f"only {p['years_in_job']} years in the current job or business"))
    if foir_limit - 0.05 < foir <= foir_limit:
        flags.append(("RG-4", f"FOIR {foir:.2%} is within 5 points of the {foir_limit:.0%} limit"))
    if product == "personal" and amount > 1_000_000:
        flags.append(("RG-5", f"unsecured amount {inr(amount)} above Rs 10,00,000"))

    return {
        "rate_percent": rate, "emi": round(new_emi, 2), "foir": round(foir, 4), "foir_limit": foir_limit,
        "total_payable": round(new_emi * months, 2),
        "failed_rules": [{"clause": c, "reason": r} for c, r in failed],
        "risk_flags": [{"clause": c, "reason": r} for c, r in flags],
        "kyc_ok": p["kyc_status"] == "verified",                                  # KYC-2
    }


def recommend(eligibility: dict, risk: dict) -> dict:
    """Combine the code checks with the LLM's purpose review into one recommendation (REJECT / REFER / APPROVE)."""
    reasons = [f"{f['clause']}: {f['reason']}" for f in eligibility["failed_rules"]]
    if risk.get("speculative_purpose"):
        reasons.append(f"LP-11: stated purpose is speculative ({risk.get('purpose_summary', '')})")
    if reasons:
        return {"decision": "REJECT", "hard_rule_failed": True, "reasons": reasons}

    flags = [f"{f['clause']}: {f['reason']}" for f in eligibility["risk_flags"]]
    if risk.get("unclear_purpose"):
        flags.append("RG-6: stated purpose is unclear or does not fit the product")
    refer = []
    if not eligibility["kyc_ok"]:
        refer.append("KYC-2: KYC is not verified; re-KYC needed before sanction")
    if len(flags) >= 2:
        refer.append(f"High risk: {len(flags)} risk flags (RG table)")
    if refer:
        return {"decision": "REFER", "hard_rule_failed": False, "reasons": refer + flags}
    return {"decision": "APPROVE", "hard_rule_failed": False,
            "reasons": flags or ["All LP rules met, no risk flags"], "risk_level": "Medium" if flags else "Low"}
