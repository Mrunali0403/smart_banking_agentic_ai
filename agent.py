"""A single LangChain tool-calling agent for an authenticated customer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from streamlit.errors import StreamlitSecretNotFoundError

from banking import assess_eligibility, calculate_emi
from rag import PolicyRetriever
from storage import BankingStore, DuplicateApplicationError, IneligibleApplicationError

FREE_OPENROUTER_FALLBACK_MODELS = (
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-26b-a4b-it:free",
    "cohere/north-mini-code:free",
)


def llm_setup_message() -> str:
    return (
        "The banking tools are available, but chat needs an LLM API key. "
        "Set `LLM_API_KEY` and `LLM_MODEL` in `.env` locally or in Streamlit Community Cloud secrets."
    )


def _streamlit_secrets() -> Any:
    import streamlit as st

    try:
        return st.secrets
    except StreamlitSecretNotFoundError:
        return {}


def get_llm_configuration(environ: dict[str, str] | None = None) -> dict[str, Any]:
    if environ is None:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        settings = {**_streamlit_secrets(), **os.environ}
    else:
        settings = environ
    api_key = settings.get("LLM_API_KEY", "").strip()
    if not api_key or api_key.lower() in {"your_api_key", "replace_me", "changeme"}:
        raise RuntimeError(llm_setup_message())
    model_name = settings.get("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b:free").strip()
    if not model_name:
        raise RuntimeError("LLM_MODEL is empty. Set it to a model supported by your provider in `.env` or Streamlit secrets.")
    try:
        temperature = float(settings.get("LLM_TEMPERATURE", "0"))
    except ValueError as exc:
        raise RuntimeError("LLM_TEMPERATURE must be a number. Check `.env` or Streamlit secrets.") from exc
    base_url = settings.get("LLM_BASE_URL", "").strip() or None
    if base_url is None and api_key.startswith("sk-or-"):
        base_url = "https://openrouter.ai/api/v1"
        if "/" not in model_name:
            model_name = f"openai/{model_name}"
    return {
        "api_key": api_key,
        "model": model_name,
        "base_url": base_url,
        "temperature": temperature,
    }


def llm_error_message(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    if status in (401, 403) or "authentication" in type(error).__name__.lower():
        return "The LLM provider rejected the configured credentials. Check LLM_API_KEY and LLM_BASE_URL in `.env` or Streamlit secrets; the key is not shown here."
    if status == 404 or "notfound" in type(error).__name__.lower():
        return "The LLM provider could not find the configured model or endpoint. Check LLM_MODEL and LLM_BASE_URL in `.env` or Streamlit secrets."
    if status == 429 or "ratelimit" in type(error).__name__.lower():
        return (
            "The configured free LLM and available free tool-capable fallback routes are currently rate limited. "
            "Retry later; the assistant will not switch to a paid model."
        )
    return "The banking assistant could not complete that request. Check your LLM provider configuration and try again."


def get_existing_loan_tool_result(store: BankingStore, customer_id: str) -> dict[str, Any]:
    loans = store.list_active_existing_loans(customer_id)
    if not loans:
        return {"found": False, "message": "No existing loans were found for your account.", "loans": []}
    return {"found": True, "loans": loans}


def create_banking_agent(
    customer_id: str,
    store: BankingStore,
    policy_retriever: PolicyRetriever,
    *,
    submission_confirmed: bool = False,
    model_override: str | None = None,
) -> Any:
    config = get_llm_configuration()
    model = ChatOpenAI(
        model=model_override or config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        temperature=config["temperature"],
    )


    @tool
    def calculate_loan_emi(loan_amount: float, annual_interest_rate: float, tenure_years: float) -> dict[str, float]:
        """Calculate a reducing-balance monthly EMI with Python. Rate is annual percent."""
        return {"monthly_emi": calculate_emi(loan_amount, annual_interest_rate, tenure_years)}

    @tool
    def check_loan_eligibility(monthly_salary: float, existing_emi: float, proposed_emi: float) -> dict[str, Any]:
        """Check eligibility using Python and replace guessed EMI with the authenticated customer's active loan EMIs."""
        if store.list_active_existing_loans(customer_id):
            recorded_emi = store.get_existing_emi(customer_id)
            if recorded_emi is None:
                return {"eligible": False, "reasons": ["An active loan is missing its recorded monthly EMI."]}
            existing_emi = recorded_emi
        result = assess_eligibility(monthly_salary, existing_emi, proposed_emi)
        return {
            "eligible": result.eligible,
            "monthly_obligation": result.monthly_obligation,
            "debt_to_income_ratio": round(result.debt_to_income_ratio, 4),
            "reasons": list(result.reasons),
            "rules": list(result.rules),
        }

    @tool
    def get_my_account_details() -> dict[str, str]:
        """Retrieve only the account details belonging to the authenticated customer."""
        customer = store.get_customer(customer_id)
        if customer is None:
            return {"error": "Authenticated customer account was not found."}
        return {
            "customer_id": customer["customer_id"],
            "name": customer["name"],
            "account_number": customer["account_number"],
            "account_type": customer["account_type"],
            "available_balance": customer["available_balance"],
        }

    @tool
    def get_my_existing_loans() -> dict[str, Any]:
        """Retrieve only the authenticated customer's existing loan accounts, never submitted applications."""
        return get_existing_loan_tool_result(store, customer_id)

    @tool
    def get_my_loan_applications() -> dict[str, Any]:
        """Retrieve only the authenticated customer's submitted applications; these are not active loans or approvals."""
        applications = store.list_applications(customer_id)
        if not applications:
            return {"found": False, "message": "No submitted loan applications were found for your account.", "applications": []}
        return {"found": True, "applications": applications}

    @tool
    def retrieve_banking_policy(question: str) -> str:
        """Retrieve relevant policy passages and source filenames for banking policy questions."""
        return policy_retriever.context_for(question)

    @tool
    def submit_loan_application(
        loan_amount: float,
        annual_interest_rate: float,
        tenure_years: float,
        monthly_salary: float,
        existing_emi: float,
    ) -> dict[str, Any]:
        """Submit an eligible application only after explicit in-chat confirmation of its reviewed details."""
        if not submission_confirmed:
            return {"submitted": False, "message": "Review the application summary in chat and reply confirm before submission."}
        if store.list_active_existing_loans(customer_id):
            recorded_emi = store.get_existing_emi(customer_id)
            if recorded_emi is None:
                return {"submitted": False, "message": "An active loan is missing its recorded monthly EMI; the application was not submitted."}
            existing_emi = recorded_emi
        try:
            saved = store.submit_loan_application(
                customer_id=customer_id,
                loan_amount=loan_amount,
                annual_interest_rate=annual_interest_rate,
                tenure_years=tenure_years,
                monthly_salary=monthly_salary,
                existing_emi=existing_emi,
                confirmed=True,
            )
        except IneligibleApplicationError as exc:
            return {"submitted": False, "message": str(exc), "reasons": list(exc.reasons)}
        except DuplicateApplicationError as exc:
            return {"submitted": False, "message": str(exc)}
        return {
            "submitted": True,
            "reference": saved["reference"],
            "status": saved["status"],
            "loan_amount": saved["loan_amount"],
            "tenure_years": saved["tenure_years"],
            "annual_interest_rate": saved["annual_interest_rate"],
            "monthly_emi": saved["monthly_emi"],
            "monthly_salary": saved["monthly_salary"],
            "existing_emi": saved["existing_emi"],
        }

    return create_agent(
        model=model,
        tools=[
            calculate_loan_emi,
            check_loan_eligibility,
            get_my_account_details,
            get_my_existing_loans,
            get_my_loan_applications,
            submit_loan_application,
            retrieve_banking_policy,
        ],
        system_prompt=(
            "You are a helpful banking support agent. Use tools for all EMI calculations and eligibility decisions; "
            "never estimate them yourself. Ask for missing amount, annual rate, tenure, salary, or existing EMI before checking. "
            "Use the authenticated-account tool for account questions, get_my_existing_loans for existing loans, "
            "and get_my_loan_applications for application status. Keep pending applications distinct from active loans. "
            "Use the policy retrieval tool for policy questions. Report tool results only; never invent account or loan records. "
            "Only personal-loan applications are supported; explain that other loan types are unavailable. "
            "Policy values are illustrative and do not constitute real bank policy or approval. Do not submit until the user explicitly confirms; "
            "the submission tool enforces this independently. Summarize results, sources, and application references accurately. "
            "A confirmed eligible application is marked Approved - Simulated. This is only a simulated status, "
            "not a real bank approval or disbursement. Never claim the customer received real bank approval."
        ),
    )


def _is_rate_limited(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    return status == 429 or "ratelimit" in type(error).__name__.lower()


def invoke_with_free_model_fallbacks(
    initial_agent: Any,
    customer_id: str,
    store: BankingStore,
    policy_retriever: PolicyRetriever,
    agent_input: dict[str, Any],
    *,
    invoke_config: dict[str, Any] | None = None,
) -> Any:
    """Retry rate-limited OpenRouter requests using free tool-capable models only."""
    try:
        return initial_agent.invoke(agent_input, config=invoke_config)
    except Exception as error:
        if not _is_rate_limited(error):
            raise
        llm_config = get_llm_configuration()
        base_url = (llm_config.get("base_url") or "").lower()
        if "openrouter.ai" not in base_url:
            raise
        last_error = error
        attempted = {llm_config["model"]}
        for model_name in FREE_OPENROUTER_FALLBACK_MODELS:
            if model_name in attempted:
                continue
            attempted.add(model_name)
            fallback_agent = create_banking_agent(
                customer_id,
                store,
                policy_retriever,
                model_override=model_name,
            )
            try:
                return fallback_agent.invoke(agent_input, config=invoke_config)
            except Exception as fallback_error:
                if not _is_rate_limited(fallback_error):
                    raise
                last_error = fallback_error
        raise last_error