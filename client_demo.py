"""Call the deployed loan desk from your laptop, like another bank system would.

    python client_demo.py                                   local:  http://127.0.0.1:8000
    python client_demo.py --url http://13.233.xx.xx:8000   your EC2 instance
    python client_demo.py --url ... --app LA-26050          a different application

The officer key is read from .env (DEMO_OFFICER_KEY), never typed on the command line.
"""
import argparse
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--app", default="LA-26054")
args = ap.parse_args()
key = os.getenv("DEMO_OFFICER_KEY", "officer-demo-key")
headers = {"X-API-Key": key}

with httpx.Client(base_url=args.url, headers=headers, timeout=120) as c:
    print("health:", c.get("/health").json())

    r = c.post("/applications", json={"application_id": args.app})
    if r.status_code == 409:
        print(f"{args.app} was already processed on this server; showing it")
        r = c.get(f"/applications/{args.app}")
    r.raise_for_status()
    s = r.json()
    print(f"\n{s['application_id']}: {s['status']}")
    if s["status"] != "awaiting_officer":
        print(json.dumps(s, indent=2))
        raise SystemExit
    a, n = s["applicant"], s["numbers"]
    print(f"applicant: {a['name']}, {a['employment_type']}, income {a['monthly_income']:,.0f}, score {a['credit_score']}, KYC {a['kyc_status']}")
    print(f"request:   {s['request']['product']} loan {s['request']['amount_inr']:,.0f} for {s['request']['tenure_months']} months; {s['request']['purpose']}")
    print(f"numbers:   EMI {n['emi']:,.2f} at {n['rate_percent']}%, FOIR {n['foir']:.1%} (limit {n['foir_limit']:.0%})")
    rec = s["recommendation"]
    print(f"\nRECOMMENDATION: {rec['decision']}")
    for reason in rec["reasons"]:
        print("  -", reason)

    choice = ""
    while choice not in ("approve", "reject"):   # the officer always decides explicitly
        choice = input("\nYour decision [approve/reject]: ").strip().lower()
    d = c.post(f"/applications/{args.app}/decision", json={"decision": choice, "note": "via client_demo"}).json()
    print(f"\nfinal: {d['final']['decision']}  status: {d['status']}\n")
    print(d["letter"])
