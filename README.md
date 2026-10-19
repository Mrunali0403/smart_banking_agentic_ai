# Smart Banking Support Agent Using Agentic AI

A simulated Streamlit banking-support application with registration/login, private per-customer account details, Python-based personal-loan EMI and eligibility checks, CSV application storage, policy-document retrieval, and one LangChain tool-calling agent.

> This is not a real banking service. Eligibility is based on the illustrative rules below and is not an offer, credit decision, or approval. Do not enter real financial credentials or personal data.

## Features

- New customer registration and sign-in; unique customer IDs; salted scrypt password hashes; duplicate email prevention.
- Session-bound account lookup. Agent tools are created for the authenticated customer and cannot look up a customer selected in chat.
- Reducing-balance EMI calculation and eligibility decisions in Python, not in the language model.
- Explicit confirmation checkbox and submit action, eligibility recheck, reference numbers, and a 24-hour identical-application duplicate guard.
- A confirmed, eligible application receives `Approved - Simulated`; this is not real-bank approval or loan disbursement. Pending applications remain separate from active loan records.
- Optional opening deposits and later deposits update the account balance and create customer-scoped transaction history. Salary is stored only with the loan application and is never added to account balance.
- Existing loan accounts are read separately from loan applications through `data/existing_loans.csv`, filtered by authenticated customer ID. A missing file is created with the expected header; fictional examples are kept in `sample_data/existing_loans.csv`.
- The Banking Assistant can gather personal-loan details across messages, ask only for missing fields, and reuse the same Python EMI, eligibility, and CSV submission functions as the Personal Loan tab.
- Policy markdown loading, overlapping chunks, deterministic local feature-hash embeddings, ranked retrieval, and source filenames. Retrieval works without an API key; the agent uses retrieved passages to answer policy questions when configured.
- Customer, loan application, and existing-loan CSV files are created automatically in `data/` on use. `BANKING_DATA_DIR` can point to a private directory.

## Fictional eligibility rules

- Reported monthly salary must be at least ₹25,000.
- Existing EMI plus proposed EMI must not exceed 50% of reported monthly salary.
- These are demonstration rules only; a result marked eligible is not real loan approval.

## Setup

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -e .env || cp .env.example .env
```

Set `LLM_API_KEY` in `.env` for OpenRouter, plus `LLM_MODEL` and `LLM_BASE_URL` for your selected model. Never commit `.env`. Without an API key, registration, sign-in, account views, loan calculations, application storage, and policy search still work; the chat tab displays setup instructions.

## Deploy to Streamlit Community Cloud

Deploy this repository's `main` branch with `app.py` as the app entry point. In the app's **Settings → Secrets**, add the OpenRouter configuration (use your own key; never commit it):

```toml
LLM_API_KEY = "your-openrouter-api-key"
LLM_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
LLM_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TEMPERATURE = "0"
```

The application reads these Streamlit secrets in Cloud and environment variables (including `.env` locally), with environment variables taking precedence. The `data/` CSV files are runtime data and may not persist across Cloud restarts; use fictional data only.

## Run

```bash
streamlit run app.py
```

Register with fictional details or sign in with an account created on this installation. The loan calculator starts with the example request (₹5,00,000, 3 years, 12% annual interest, ₹60,000 monthly salary, ₹5,000 existing EMI). Review the calculated result and explicitly confirm before submitting. A passing and confirmed application is saved locally with status `Approved - Simulated`, not as real bank approval.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover EMI, eligibility, registration validation, password hashing, CSV persistence, duplicate application prevention, and policy retrieval. No API key or network access is needed to run them.

## Configuration

- `LLM_API_KEY`: provider API credential (required only for agent chat).
- `LLM_MODEL`: provider model name; the configured free OpenRouter example is `nvidia/nemotron-3-super-120b-a12b:free`.
- `LLM_BASE_URL`: OpenAI-compatible provider endpoint; example is OpenRouter.
- `LLM_TEMPERATURE`: optional model temperature, defaults to `0`.
- `BANKING_DATA_DIR`: optional path for generated customer/application CSVs.
