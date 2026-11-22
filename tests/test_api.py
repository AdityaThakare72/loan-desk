"""Offline API tests: the three LLM agents are replaced by simple fakes, so the whole flow
(intake -> rules -> risk -> recommendation -> officer -> letter) runs with no key and no cost.

    python -m unittest discover -s tests -v
"""
import os
import re
import tempfile
import unittest

os.environ["OPENROUTER_API_KEY"] = "test-key-not-used"
os.environ["OFFICER_API_KEYS"] = "test-key:Test Officer"
os.environ["LOG_DIR"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

from app import agents  # noqa: E402
from app.main import app  # noqa: E402


def fake_intake(text):
    aid = re.search(r"A\d{4}", text)
    amount = re.search(r"([\d.]+) lakh", text)
    years = re.search(r"(\d+) years", text)
    return {"applicant_id": aid.group(0) if aid else None, "product": "personal" if "personal" in text else None,
            "amount_inr": float(amount.group(1)) * 100000 if amount else None,
            "tenure_months": int(years.group(1)) * 12 if years else None,
            "purpose": "crypto" if "crypto" in text else ("a wedding" if "wedding" in text else None)}


def fake_risk(request, profile, eligibility):
    spec = "crypto" in request["purpose"]
    return {"purpose_summary": request["purpose"], "speculative_purpose": spec, "unclear_purpose": False,
            "risk_level": "Low", "concerns": [], "questions_for_applicant": []}


agents.intake = fake_intake
agents.review_risk = fake_risk
agents.write_letter = lambda name, request, eligibility, final: f"Dear {name}, decision: {final['decision']}"

KEY = {"X-API-Key": "test-key"}
client = TestClient(app, raise_server_exceptions=False)


class Api(unittest.TestCase):
    def test_health_needs_no_key(self):
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["policy_clauses"], 24)

    def test_key_required(self):
        self.assertEqual(client.post("/applications", json={"text": "hi"}).status_code, 401)
        self.assertEqual(client.post("/applications", json={"text": "hi"}, headers={"X-API-Key": "wrong"}).status_code, 401)

    def test_validation(self):
        self.assertEqual(client.post("/applications", json={}, headers=KEY).status_code, 422)
        self.assertEqual(client.post("/applications", json={"text": "x" * 5000}, headers=KEY).status_code, 422)

    def test_missing_details(self):
        r = client.post("/applications", json={"text": "I need a personal loan urgently"}, headers=KEY).json()
        self.assertEqual(r["status"], "needs_info")
        self.assertIn("applicant_id", r["missing"])

    def test_unknown_customer(self):
        r = client.post("/applications", json={"text": "A9999 personal loan 2 lakh for 2 years for a wedding"}, headers=KEY)
        self.assertEqual(r.json()["status"], "unknown_applicant")

    def find_applicant(self, **want):
        from app.data import get_profile, applicants
        for aid in applicants.index:
            p = get_profile(aid)
            if all(p[k] == v for k, v in want.items()):
                return aid
        raise AssertionError("no applicant found")

    def test_full_flow_and_letter(self):
        aid = self.find_applicant(kyc_status="verified", defaults_24m=0)
        r = client.post("/applications", json={"text": f"{aid} personal loan 1 lakh for 2 years for a wedding"}, headers=KEY).json()
        self.assertEqual(r["status"], "awaiting_officer")
        app_id = r["application_id"]
        self.assertEqual(client.get(f"/applications/{app_id}", headers=KEY).json()["status"], "awaiting_officer")
        d = client.post(f"/applications/{app_id}/decision", json={"decision": "reject", "note": "test"}, headers=KEY).json()
        self.assertEqual(d["status"], "declined")
        self.assertIn("Dear", d["letter"])
        # deciding twice is a conflict
        again = client.post(f"/applications/{app_id}/decision", json={"decision": "approve"}, headers=KEY)
        self.assertEqual(again.status_code, 409)

    def test_hard_rule_cannot_be_overridden(self):
        aid = self.find_applicant(kyc_status="verified")
        r = client.post("/applications", json={"text": f"{aid} personal loan 2 lakh for 2 years for crypto"}, headers=KEY).json()
        self.assertEqual(r["recommendation"]["decision"], "REJECT")
        d = client.post(f"/applications/{r['application_id']}/decision", json={"decision": "approve"}, headers=KEY).json()
        self.assertEqual(d["final"]["decision"], "REJECT")
        self.assertIn("override blocked", d["officer"]["note"])

    def test_queue_and_unknown_ids(self):
        self.assertEqual(len(client.get("/queue", headers=KEY).json()), 62)
        self.assertEqual(client.get("/applications/LA-NOPE", headers=KEY).status_code, 404)
        self.assertEqual(client.post("/applications", json={"application_id": "LA-NOPE"}, headers=KEY).status_code, 404)


if __name__ == "__main__":
    unittest.main()
