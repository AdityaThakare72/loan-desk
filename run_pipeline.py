"""Run one application through the pipeline in the terminal and watch every stage. You are the officer.

    python run_pipeline.py LA-26001                 an application from data/applications.csv
    python run_pipeline.py --text "Customer A1007, personal loan 3 lakh for 2 years for my wedding"
    python run_pipeline.py --list                   show the queue
"""
import argparse
import json
import uuid

from langgraph.types import Command

from app import data
from app.config import check

check_settings = check

ap = argparse.ArgumentParser()
ap.add_argument("application_id", nargs="?")
ap.add_argument("--text")
ap.add_argument("--list", action="store_true")
args = ap.parse_args()

if args.list:
    for r in data.applications.reset_index().itertuples():
        print(f"{r.application_id}  {r.application_text[:90]!r}")
    raise SystemExit

check_settings()
from app.pipeline import graph  # noqa: E402  (import after the settings check)

if args.text:
    app_id, text = f"LA-{uuid.uuid4().hex[:8].upper()}", args.text
else:
    app_id = args.application_id or "LA-26001"
    text = data.get_application_text(app_id)
    if text is None:
        raise SystemExit(f"No application {app_id}. Try --list")

print(f"=== {app_id} ===\n{text}\n")
cfg = {"configurable": {"thread_id": app_id}}


def show(update):
    for node, out in update.items():
        if node == "__interrupt__":
            continue
        print(f"--- {node} " + "-" * (50 - len(node)))
        for key, value in (out or {}).items():
            if key == "profile":
                value = {k: value[k] for k in ["name", "age", "employment_type", "monthly_income", "credit_score",
                                               "existing_emi", "kyc_status"]}
            text = json.dumps(value, indent=2, ensure_ascii=False) if not isinstance(value, str) else value
            print(f"{key}: {text}")


# stream_mode="updates": print what each node returned, as it happens
for update in graph.stream({"text": text}, cfg, stream_mode="updates"):
    show(update)

state = graph.get_state(cfg)
if state.values.get("status") != "awaiting_officer":
    print(f"\nStopped: {state.values.get('status')}")
    raise SystemExit

rec = state.values["recommendation"]
print(f"\n>>> RECOMMENDATION: {rec['decision']}")
for r in rec["reasons"]:
    print("    -", r)
choice = ""
while choice not in ("approve", "reject"):   # the officer always decides explicitly
    choice = input("\nOfficer decision [approve/reject]: ").strip().lower()
note = input("Note (optional): ").strip()

for update in graph.stream(Command(resume={"decision": choice, "note": note}), cfg, stream_mode="updates"):
    show(update)
print("\nstatus:", graph.get_state(cfg).values.get("status"))
