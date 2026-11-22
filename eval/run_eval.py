"""Evaluate the pipeline's recommendations against the expected decisions (from the policy rules).

    python eval/run_eval.py              first 15 applications (2 LLM calls each)
    python eval/run_eval.py --all        all 60

The pipeline stops at officer review, so no letters are written and nothing is decided.
A mismatch usually means the intake agent misread the message (amount, tenure) or the
risk reviewer misjudged the purpose (LP-11): both are printed so you can see which.
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import check  # noqa: E402

check()
from app import data  # noqa: E402
from app.pipeline import graph  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--all", action="store_true")
args = ap.parse_args()
expected = json.load(open(ROOT / "eval/expected_decisions.json"))
cases = expected if args.all else expected[:15]

ok, rows, t0 = 0, [], time.perf_counter()
for i, e in enumerate(cases, 1):
    cfg = {"configurable": {"thread_id": f"eval-{e['application_id']}-{time.time()}"}}
    graph.invoke({"text": data.get_application_text(e["application_id"])}, cfg)
    s = graph.get_state(cfg).values
    got = (s.get("recommendation") or {}).get("decision", s.get("status"))
    req = s.get("request") or {}
    intake_ok = (req.get("applicant_id") == e["applicant_id"] and req.get("product") == e["product"]
                 and abs((req.get("amount_inr") or 0) - e["amount"]) < 1 and req.get("tenure_months") == e["tenure_months"])
    match = got == e["expected"]
    ok += match
    rows.append((e["application_id"], e["expected"], got, intake_ok))
    print(f"{i:>2}. {e['application_id']}  expected {e['expected']:<7} got {got:<17} "
          f"{'ok ' if match else 'BAD'}  intake {'ok' if intake_ok else 'WRONG: ' + json.dumps(req)}")

print(f"\n{ok} of {len(cases)} recommendations match ({ok / len(cases):.0%}) in {time.perf_counter() - t0:.0f} s")
print("intake correct:", sum(r[3] for r in rows), "of", len(rows))
print("confusions:", dict(Counter((r[1], r[2]) for r in rows if r[1] != r[2])) or "none")
