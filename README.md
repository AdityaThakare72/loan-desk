# Sahyadri Bank Loan Desk

A multi-agent system that processes retail loan applications. It reads a customer's message, checks it against the bank's data and lending policy, flags risks, recommends a decision, waits for a loan officer's approval, and writes the decision letter. It runs as a FastAPI service in Docker and is deployed on AWS EC2.

All data is fictional (Sahyadri Digital Bank, for training).

## How it works

```
customer message
   |
   v
[1] intake agent (LLM)        message -> structured request (id, product, amount, tenure, purpose)
   |   missing details? -> stop: "needs_info"
   v
[2] load profile (code)       applicant + credit bureau + bank statements from CSV
   |   unknown customer? -> stop
   v
[3] check rules (code)        EMI, FOIR, hard rules LP-3..LP-9, risk flags RG-1..RG-5
   |   a hard rule failed? -> skip [4]
   v
[4] risk reviewer (LLM)       purpose vs LP-11 / RG-6, using policy clauses found in the PDFs
   v
[5] recommend (code)          REJECT / REFER / APPROVE with clause ids
   v
[6] officer review (human)    graph pauses (interrupt); a hard rule cannot be overridden
   v
[7] letter writer (LLM)       sanction or decline letter with the exact numbers
```

Design rules: numbers and rule checks in code, never in the LLM; each LLM agent has one job and a structured output; a human makes the final decision; every action goes to an audit log.

## Project files

| Path | What it is |
|---|---|
| `data/applicants.csv` | 120 applicants: age, city, employment, income, KYC status |
| `data/credit_bureau.csv` | credit score, active loans, existing EMI, defaults, enquiries |
| `data/bank_statements.csv` | average balance, salary credits, bounced payments (6 months) |
| `data/applications.csv` | 62 incoming applications as free text (email, branch, web form) |
| `policies/*.pdf` | Retail Lending Policy, Credit Risk Guidelines, KYC Policy |
| `app/config.py` | settings from environment variables; fails fast if a key is missing |
| `app/data.py` | loads the CSVs; `get_profile(applicant_id)` |
| `app/rules.py` | the policy as code: EMI, FOIR, LP and RG checks, recommendation |
| `app/policies.py` | PDF -> one chunk per clause -> BM25 search |
| `app/agents.py` | intake agent, risk reviewer, letter writer |
| `app/pipeline.py` | the LangGraph graph with the officer interrupt |
| `app/main.py` | the FastAPI service, officer keys, audit log |
| `run_pipeline.py` | run one application in the terminal and watch every stage |
| `eval/run_eval.py` | compare recommendations with the expected decisions |
| `tests/` | 22 offline tests (no LLM, no key) |
| `Dockerfile` | the container image |

## Run locally

```
uv venv --python 3.12
.venv\Scripts\activate                  (Windows)   source .venv/bin/activate   (Mac/Linux)
uv pip install -r requirements.txt
copy .env.example .env                  then put your OPENROUTER_API_KEY in .env
```

```
python -m unittest discover -s tests -v          # 22 offline tests
python run_pipeline.py --list                    # the queue
python run_pipeline.py LA-26054                  # one application, stage by stage; you are the officer
python eval/run_eval.py                          # 15 applications vs expected decisions
uvicorn app.main:app --reload --port 8000        # the API; open http://127.0.0.1:8000/docs
```

## The API

All endpoints except `/health` need the header `X-API-Key` with an officer key from `OFFICER_API_KEYS`.

| Method and path | Does |
|---|---|
| `GET /health` | status, model, number of policy clauses |
| `GET /queue` | applications waiting in `data/applications.csv` |
| `POST /applications` | body `{"application_id": "LA-26054"}` or `{"text": "..."}`; runs until officer review |
| `GET /applications/{id}` | current state |
| `POST /applications/{id}/decision` | body `{"decision": "approve" or "reject", "note": "...", "conditions": "..."}`; writes the letter |

Status codes: 401 no or wrong key, 404 unknown id, 409 already processed or not waiting, 422 invalid body, 502 the LLM failed.

## Run with Docker

```
docker build -t loan-desk .
docker run -d --name loan-desk -p 8000:8000 --env-file .env loan-desk
docker logs -f loan-desk
```

## Deploy on AWS EC2

See `AWS_EC2_Deployment_Guide` in the session folder: launch a free-tier `t3.micro` instance, install Docker, clone this repository, create `.env` on the server, build and run the container, open port 8000 in the security group.

## Limits and next steps

- Paused applications are kept in memory (`InMemorySaver`): a container restart loses applications waiting for a decision. Next: a persistent checkpointer (Postgres or SQLite on a volume).
- Data is read from CSV files baked into the image. Next: a database and an S3 bucket for policies.
- The audit log is a file inside the container. Next: CloudWatch Logs or a database table.
- HTTP only on port 8000. Next: a domain, HTTPS (a load balancer or Nginx with a certificate).
- Officer keys are static. Next: real login (Cognito or the bank's SSO).
