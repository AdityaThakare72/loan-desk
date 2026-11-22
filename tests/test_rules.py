"""Offline tests of the policy rules. No LLM, no key, no cost: run them on every change.

    python -m unittest discover -s tests -v
"""
import os
import unittest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")
os.environ.setdefault("OFFICER_API_KEYS", "test-key:Test Officer")

from app import policies, rules  # noqa: E402
from app.data import get_profile  # noqa: E402

BASE = {"name": "Test", "age": 30, "employment_type": "salaried", "years_in_job": 4, "monthly_income": 60000,
        "kyc_status": "verified", "credit_score": 780, "existing_emi": 0, "defaults_24m": 0,
        "enquiries_6m": 0, "bounced_6m": 0}


def check(product="personal", amount=300000, months=36, **changes):
    return rules.check_eligibility({"product": product, "amount_inr": amount, "tenure_months": months}, {**BASE, **changes})


def failed(result):
    return [f["clause"] for f in result["failed_rules"]]


def flags(result):
    return [f["clause"] for f in result["risk_flags"]]


class Rules(unittest.TestCase):
    def test_emi_matches_known_value(self):
        self.assertAlmostEqual(rules.emi(300000, 11.75, 24), 14087.04, places=2)

    def test_clean_application_passes(self):
        r = check()
        self.assertEqual(failed(r), [])
        self.assertEqual(flags(r), [])
        self.assertEqual(r["rate_percent"], 11.5)           # score >= 750

    def test_rate_depends_on_score(self):
        self.assertEqual(check(credit_score=720)["rate_percent"], 13.0)

    def test_each_hard_rule(self):
        self.assertIn("LP-3", failed(check(credit_score=690)))
        self.assertIn("LP-4", failed(check(defaults_24m=1)))
        self.assertIn("LP-5", failed(check(monthly_income=24000, amount=100000)))
        self.assertIn("LP-6", failed(check(product="home", amount=3000000, months=300, age=45)))
        self.assertIn("LP-7", failed(check(months=72)))
        self.assertIn("LP-8", failed(check(amount=1600000, months=60, monthly_income=200000)))
        self.assertIn("LP-9", failed(check(existing_emi=25000, amount=500000)))

    def test_self_employed_has_lower_foir_limit(self):
        r = check(employment_type="self_employed", years_in_job=5, existing_emi=22000, amount=200000)   # FOIR 47.7%: fine if salaried
        self.assertEqual(r["foir_limit"], 0.45)
        self.assertIn("LP-9", failed(r))

    def test_risk_flags(self):
        self.assertIn("RG-1", flags(check(enquiries_6m=4)))
        self.assertIn("RG-2", flags(check(bounced_6m=2)))
        self.assertIn("RG-3", flags(check(years_in_job=0.5)))
        self.assertIn("RG-4", flags(check(existing_emi=14000, amount=400000)))   # FOIR 45.3%
        self.assertIn("RG-5", flags(check(amount=1100000, months=60, monthly_income=150000)))


class Recommend(unittest.TestCase):
    def test_hard_rule_means_reject(self):
        rec = rules.recommend(check(credit_score=600), {})
        self.assertEqual(rec["decision"], "REJECT")
        self.assertTrue(rec["hard_rule_failed"])

    def test_speculative_purpose_means_reject(self):
        rec = rules.recommend(check(), {"speculative_purpose": True, "purpose_summary": "crypto"})
        self.assertEqual(rec["decision"], "REJECT")
        self.assertIn("LP-11", rec["reasons"][0])

    def test_two_flags_mean_refer(self):
        self.assertEqual(rules.recommend(check(enquiries_6m=5, bounced_6m=3), {})["decision"], "REFER")

    def test_kyc_not_verified_means_refer(self):
        self.assertEqual(rules.recommend(check(kyc_status="expired"), {})["decision"], "REFER")

    def test_one_flag_still_approve(self):
        rec = rules.recommend(check(enquiries_6m=4), {})
        self.assertEqual((rec["decision"], rec["risk_level"]), ("APPROVE", "Medium"))


class DataAndPolicies(unittest.TestCase):
    def test_profile_lookup(self):
        p = get_profile("A1001")
        self.assertEqual(p["applicant_id"], "A1001")
        for key in ["monthly_income", "credit_score", "existing_emi", "bounced_6m", "kyc_status"]:
            self.assertIn(key, p)
        self.assertIsNone(get_profile("A9999"))

    def test_every_clause_is_a_chunk(self):
        ids = {c["clause"] for c in policies.CHUNKS}
        for c in ["LP-2", "LP-9", "LP-11", "RG-1", "RG-6", "KYC-2"]:
            self.assertIn(c, ids)

    def test_search_finds_the_speculation_clause(self):
        self.assertEqual(policies.search("investing in shares and crypto trading", k=1)[0]["clause"], "LP-11")


if __name__ == "__main__":
    unittest.main()
