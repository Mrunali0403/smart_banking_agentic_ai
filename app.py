from __future__ import annotations

import os
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from auth import hash_password, validate_registration, verify_password
from banking import CONFIGURED_ANNUAL_INTEREST_RATE, ELIGIBILITY_RULES, assess_eligibility, calculate_emi
from loan_chat import (
    is_personal_loan_request,
    is_submission_cancellation,
    is_submission_confirmation,
    is_unsupported_loan_application,
    missing_fields_prompt,
    missing_personal_loan_fields,
    requested_unsupported_loan,
    submit_confirmed_personal_loan,
    update_personal_loan_details,
)
from rag import PolicyRetriever, format_policy_markdown
from storage import (
    ApplicationConfirmationError,
    BankingStore,
    DuplicateApplicationError,
    IneligibleApplicationError,
)


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
store = BankingStore(os.getenv("BANKING_DATA_DIR", str(ROOT / "data")))
policy_retriever = PolicyRetriever(ROOT / "policies")

st.set_page_config(page_title="Smart Banking Support", page_icon="₹", layout="wide")
st.markdown(
    """
    <style>
    :root { --ink: #18352e; --green: #1f6b55; --mint: #e7f1eb; --gold: #d9a441; --paper: #f5f7f4; }
    .stApp { background: var(--paper); color: var(--ink); }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMetric"] { background: white; border: 1px solid #dce5de; border-radius: 8px; padding: 14px 16px; }
    .stApp, .stMarkdown, .stText, .stMetric { font-family: "Aptos", "Segoe UI", sans-serif; }
    .banking-title { color: var(--ink); font-size: 1.8rem; font-weight: 700; margin-bottom: 0; }
    .banking-subtitle { color: #53665d; margin-top: .25rem; }
    .rule-note { border-left: 4px solid var(--gold); padding: .85rem 1rem; margin: .75rem 0 1rem; background: #fff9e9; border-radius: 0 6px 6px 0; line-height: 1.6; }
    [data-testid="stMetricLabel"] { color: #53665d; }
    [data-testid="stMetricValue"] { color: var(--ink); }
    .stButton > button[kind="primary"] { background: var(--green); border-color: var(--green); }
    div[data-testid="stTabs"] { margin-top: .75rem; }
    [data-testid="stCaptionContainer"] { color: #53665d; }
    @media (max-width: 640px) { [data-testid="stMetric"] { padding: 10px; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def show_login() -> None:
    st.markdown('<p class="banking-title">Smart Banking Support</p>', unsafe_allow_html=True)
    register_tab, login_tab = st.tabs(["Create account", "Sign in"])
    with register_tab:
        with st.form("registration_form", clear_on_submit=True):
            name = st.text_input("Full name")
            email = st.text_input("Email address")
            phone = st.text_input("Phone number", placeholder="+91 98765 43210")
            password = st.text_input("Password", type="password", help="Use at least 8 characters.")
            opening_deposit = st.number_input(
                "Opening deposit (optional, ₹)", min_value=0.0, value=0.0, step=500.0
            )
            submitted = st.form_submit_button("Create account", type="primary")
        if submitted:
            try:
                clean_name, clean_email, clean_phone = validate_registration(name, email, phone, password)
                customer_id = f"CUS-{uuid4().hex[:10].upper()}"
                store.create_customer({
                    "customer_id": customer_id,
                    "name": clean_name,
                    "email": clean_email,
                    "phone": clean_phone,
                    "password_hash": hash_password(password),
                    "account_number": f"****{uuid4().int % 10000:04d}",
                    "account_type": "Savings Account",
                }, opening_deposit=opening_deposit)
                st.session_state.customer_id = customer_id
                st.success("Account created. You are signed in.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email address", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            customer = store.find_customer_by_email(email)
            if customer and verify_password(password, customer["password_hash"]):
                st.session_state.customer_id = customer["customer_id"]
                st.rerun()
            st.error("Email or password is incorrect.")
    st.caption("Simulated banking application. It does not provide real bank approval.")


def logout() -> None:
    for key in (
        "customer_id",
        "agent_messages",
        "pending_application",
        "last_application",
        "last_deposit",
        "chat_loan_details",
        "chat_loan_review",
        "chat_loan_active",
    ):
        st.session_state.pop(key, None)
    st.rerun()


def show_account(customer: dict[str, str]) -> None:
    st.subheader("My Account")
    st.metric("Account number", customer["account_number"])
    st.write(f"**Account type:** {customer['account_type']}")
    st.write(f"**Available balance:** ₹{float(customer['available_balance']):,.2f}")
    st.caption(f"Customer ID: {customer['customer_id']}")
    deposit_receipt = st.session_state.get("last_deposit")
    if deposit_receipt and deposit_receipt["customer_id"] == customer["customer_id"]:
        st.success(f"Deposit recorded: ₹{deposit_receipt['amount']:,.2f}. Transaction {deposit_receipt['transaction_id']}.")


def show_deposits(customer_id: str) -> None:
    st.subheader("Deposit Money")
    with st.form("deposit_money_form", clear_on_submit=True):
        amount = st.number_input("Deposit amount (₹)", min_value=1.0, value=500.0, step=500.0)
        submitted = st.form_submit_button("Deposit")
    if submitted:
        try:
            customer, transaction = store.deposit_money(customer_id, amount)
        except (OSError, ValueError) as exc:
            st.error(f"Deposit failed: {exc}")
        else:
            st.session_state.last_deposit = {
                "customer_id": customer["customer_id"],
                "amount": float(transaction["amount"]),
                "transaction_id": transaction["transaction_id"],
            }
            st.success(f"Deposit recorded. Updated balance: ₹{float(customer['available_balance']):,.2f}")
            st.caption(f"Transaction reference: {transaction['transaction_id']}")
            st.rerun()
    with st.expander("Transaction history"):
        transactions = sorted(
            store.list_transactions(customer_id),
            key=lambda transaction: transaction["created_at"],
            reverse=True,
        )
        if not transactions:
            st.caption("No account transactions have been recorded.")
        for transaction in transactions:
            st.write(
                f"**{transaction['transaction_type']}** · "
                f"₹{float(transaction['amount']):,.2f} · "
                f"Balance after: ₹{float(transaction['balance_after']):,.2f} · "
                f"{transaction['transaction_id']}"
            )


def show_loan_calculator(customer_id: str) -> None:
    st.subheader("Personal Loan")
    with st.form("loan_check_form"):
        amount = st.number_input("Loan amount (₹)", min_value=1_000.0, value=500_000.0, step=10_000.0)
        annual_rate = st.number_input(
            "Annual interest rate (%)",
            min_value=0.0,
            value=CONFIGURED_ANNUAL_INTEREST_RATE,
            step=0.25,
        )
        tenure = st.number_input("Tenure (years)", min_value=1, max_value=30, value=3, step=1)
        salary = st.number_input("Monthly salary (₹)", min_value=1_000.0, value=60_000.0, step=1_000.0)
        active_loans = store.list_active_existing_loans(customer_id)
        recorded_existing_emi = store.get_existing_emi(customer_id)
        if active_loans and recorded_existing_emi is not None:
            st.metric("Existing EMI from active loans", f"₹{recorded_existing_emi:,.2f}")
            existing_emi = recorded_existing_emi
        else:
            if active_loans:
                st.warning("An active loan is missing its EMI value. Enter the combined monthly EMI to continue.")
            else:
                st.info("No existing loan EMI is linked to this account. Enter your other monthly EMIs, or 0 if you have none.")
            existing_emi = st.number_input(
                "Existing monthly EMI (₹)", min_value=0.0, value=0.0, step=500.0,
                key=f"existing_emi_{customer_id}",
            )
        checked = st.form_submit_button("Calculate and check eligibility", type="primary")
    if checked:
        try:
            emi = calculate_emi(amount, annual_rate, tenure)
            eligibility = assess_eligibility(salary, existing_emi, emi)
            st.session_state.pending_application = {
                "customer_id": customer_id,
                "loan_amount": amount,
                "annual_interest_rate": annual_rate,
                "tenure_years": tenure,
                "monthly_salary": salary,
                "existing_emi": existing_emi,
                "monthly_emi": emi,
                "eligible": eligibility.eligible,
            }
            st.session_state.pop("last_application", None)
        except ValueError as exc:
            st.error(str(exc))
    pending = st.session_state.get("pending_application")
    if pending and pending["customer_id"] == customer_id:
        eligibility = assess_eligibility(pending["monthly_salary"], pending["existing_emi"], pending["monthly_emi"])
        st.markdown("#### Loan Summary")
        summary_values = [
            ("Loan amount", f"₹{pending['loan_amount']:,.0f}"),
            ("Tenure", f"{pending['tenure_years']} years"),
            ("Interest rate", f"{pending['annual_interest_rate']:.2f}% p.a."),
            ("Monthly EMI", f"₹{pending['monthly_emi']:,.2f}"),
            ("Existing EMI", f"₹{pending['existing_emi']:,.2f}"),
            ("Combined EMI", f"₹{eligibility.monthly_obligation:,.2f}"),
            ("Monthly salary", f"₹{pending['monthly_salary']:,.2f}"),
            ("Loan Eligibility", "Eligible" if eligibility.eligible else "Not eligible"),
        ]
        for offset in range(0, len(summary_values), 4):
            columns = st.columns(4)
            for column, (label, value) in zip(columns, summary_values[offset : offset + 4]):
                column.metric(label, value)
        st.markdown('<div class="rule-note"><strong>Illustrative eligibility rules</strong><br>' + "<br>".join(ELIGIBILITY_RULES) + "</div>", unsafe_allow_html=True)
        if eligibility.reasons:
            for reason in eligibility.reasons:
                st.warning(reason)
        if eligibility.eligible:
            st.markdown("#### Application Summary")
            with st.form("confirm_application_form"):
                confirmed = st.checkbox("I have reviewed and confirm these application details.")
                submit = st.form_submit_button("Submit application", type="primary")
            if submit:
                if not confirmed:
                    st.error("Check the confirmation box before submitting.")
                else:
                    try:
                        saved = store.submit_loan_application(
                            customer_id=customer_id,
                            loan_amount=pending["loan_amount"],
                            annual_interest_rate=pending["annual_interest_rate"],
                            tenure_years=pending["tenure_years"],
                            monthly_salary=pending["monthly_salary"],
                            existing_emi=pending["existing_emi"],
                            confirmed=True,
                        )
                        st.session_state.last_application = saved
                        st.session_state.pop("pending_application", None)
                        st.rerun()
                    except (ApplicationConfirmationError, DuplicateApplicationError, IneligibleApplicationError, ValueError) as exc:
                        st.error(str(exc))
                    except OSError:
                        st.error("The application could not be saved. No submission was recorded; please try again.")


def show_applications(customer_id: str) -> None:
    st.subheader("Application Status")
    applications = sorted(store.list_applications(customer_id), key=lambda row: row["created_at"], reverse=True)
    if not applications:
        st.caption("No applications have been submitted yet.")
        return
    for application in applications:
        with st.expander(f"{application['reference']} · {application['status']}"):
            fields = st.columns(4)
            fields[0].metric("Loan amount", f"₹{float(application['loan_amount']):,.0f}")
            fields[1].metric("Tenure", f"{float(application['tenure_years']):g} years")
            fields[2].metric("Interest rate", f"{float(application['annual_interest_rate']):.2f}% p.a.")
            fields[3].metric("Monthly EMI", f"₹{float(application['monthly_emi']):,.2f}")


def show_existing_loans(customer_id: str) -> None:
    st.subheader("Existing Loans")
    loans = store.list_active_existing_loans(customer_id)
    if not loans:
        st.caption("No existing loans were found for your account.")
        return
    for loan in loans:
        title = f"{loan.get('loan_type') or 'Loan'} · {loan.get('status') or 'Status unavailable'}"
        with st.expander(title):
            columns = st.columns(4)
            columns[0].metric("Loan amount", f"₹{float(loan['loan_amount']):,.2f}")
            columns[1].metric("Monthly EMI", f"₹{float(loan['monthly_emi']):,.2f}")
            columns[2].metric(
                "Outstanding balance",
                f"₹{float(loan['outstanding_balance']):,.2f}" if loan.get("outstanding_balance") else "Not provided",
            )
            columns[3].metric("Loan status", loan.get("status") or "Not provided")


def show_submission_success() -> None:
    application = st.session_state.get("last_application")
    if not application:
        return
    st.success("Your loan application has been submitted successfully.")
    st.caption(f"Reference: {application['reference']} · Status: {application['status']}")
    eligibility = assess_eligibility(
        float(application["monthly_salary"]),
        float(application["existing_emi"]),
        float(application["monthly_emi"]),
    )
    fields = st.columns(5)
    fields[0].metric("Loan amount", f"₹{float(application['loan_amount']):,.0f}")
    fields[1].metric("Tenure", f"{float(application['tenure_years']):g} years")
    fields[2].metric("Interest rate", f"{float(application['annual_interest_rate']):.2f}% p.a.")
    fields[3].metric("Estimated EMI", f"₹{float(application['monthly_emi']):,.2f}")
    fields[4].metric(
        "Simulated approval",
        "Approved - Simulated" if eligibility.eligible and application["status"] == "Approved - Simulated" else "Not approved",
    )


def show_policy_search() -> None:
    st.subheader("Policy Library")
    st.caption("Policy values are illustrative and are not statements of real bank policy.")
    with st.form("policy_search_form"):
        question = st.text_input("Search banking policies", placeholder="What are the loan eligibility rules?")
        search = st.form_submit_button("Search policy")
    if search:
        results = policy_retriever.retrieve(question)
        if not results:
            st.info("Enter a question to search the available policy documents.")
        for result in results:
            st.caption(f"Source file: {result['source']}")
            st.markdown(format_policy_markdown(str(result["text"])))


def show_chat_loan_review(customer_id: str) -> None:
    application = st.session_state.get("chat_loan_review")
    if not application:
        return
    if application["customer_id"] != customer_id:
        st.session_state.pop("chat_loan_review", None)
        return
    eligibility = assess_eligibility(
        application["monthly_salary"],
        application["existing_emi"],
        application["monthly_emi"],
    )
    st.markdown("#### Application Review")
    values = [
        ("Loan amount", f"₹{application['loan_amount']:,.0f}"),
        ("Tenure", f"{application['tenure_years']:g} years"),
        ("Interest rate", f"{application['annual_interest_rate']:.2f}% p.a."),
        ("Estimated EMI", f"₹{application['monthly_emi']:,.2f}"),
        ("Existing EMI", f"₹{application['existing_emi']:,.2f}"),
        ("Combined EMI", f"₹{eligibility.monthly_obligation:,.2f}"),
        ("Monthly salary", f"₹{application['monthly_salary']:,.2f}"),
        ("Loan Eligibility", "Eligible" if eligibility.eligible else "Not eligible"),
    ]
    for offset in range(0, len(values), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, values[offset : offset + 4]):
            column.metric(label, value)
    if not eligibility.eligible:
        for reason in eligibility.reasons:
            st.error(reason)
        st.caption("This application cannot be submitted under the configured eligibility rules.")
        return
    st.info("To submit, reply **confirm** in this chat. Reply **cancel** to discard the review.")


def show_chat(customer: dict[str, str]) -> None:
    st.subheader("Banking Assistant")
    st.caption("Ask about your account, loan EMI, eligibility, or banking policies.")
    try:
        from agent import create_banking_agent

        agent = create_banking_agent(
            customer["customer_id"],
            store,
            policy_retriever,
            submission_confirmed=False,
        )
    except RuntimeError as exc:
        st.info(str(exc))
        return
    except (ImportError, ValueError) as exc:
        st.error(f"Could not configure the AI agent: {exc}")
        return
    messages = st.session_state.setdefault("agent_messages", [])
    for message in messages:
        if getattr(message, "type", "") in ("human", "ai"):
            with st.chat_message("user" if message.type == "human" else "assistant"):
                st.markdown(message.content if isinstance(message.content, str) else str(message.content))
    show_chat_loan_review(customer["customer_id"])
    if prompt := st.chat_input("Message your banking agent"):
        from langchain_core.messages import AIMessage, HumanMessage

        messages.append(HumanMessage(content=prompt))
        review = st.session_state.get("chat_loan_review")
        if review and review.get("customer_id") == customer["customer_id"]:
            if is_submission_confirmation(prompt):
                try:
                    saved = submit_confirmed_personal_loan(
                        store,
                        customer["customer_id"],
                        review,
                        confirmed=True,
                    )
                except (ApplicationConfirmationError, DuplicateApplicationError, IneligibleApplicationError, ValueError) as exc:
                    messages.append(AIMessage(content=f"The application was not submitted: {exc}"))
                    st.session_state.pop("chat_loan_review", None)
                    st.rerun()
                except OSError:
                    messages.append(AIMessage(content="The application could not be saved, so it was not submitted. Please try again."))
                    st.rerun()
                st.session_state.last_application = saved
                st.session_state.pop("chat_loan_review", None)
                st.session_state.pop("chat_loan_details", None)
                st.session_state.chat_loan_active = False
                messages.append(AIMessage(content=(
                    "Your loan application has been submitted successfully. "
                    f"Reference: {saved['reference']}. Status: {saved['status']}. "
                    "This is a simulated approval only; it is not a real bank approval or disbursement."
                )))
                st.rerun()
            if is_submission_cancellation(prompt):
                st.session_state.pop("chat_loan_review", None)
                st.session_state.pop("chat_loan_details", None)
                st.session_state.chat_loan_active = False
                messages.append(AIMessage(content="The loan application review was cancelled; nothing was submitted."))
                st.rerun()
            if not is_personal_loan_request(prompt):
                messages.append(AIMessage(content="Please reply **confirm** to submit this application or **cancel** to discard it."))
                st.rerun()
            st.session_state.pop("chat_loan_review", None)
        unsupported = requested_unsupported_loan(prompt)
        if unsupported and is_unsupported_loan_application(prompt):
            messages.append(AIMessage(content=(
                f"{unsupported.title()} loan applications are not supported here. "
                "I can help with a personal-loan eligibility check and application."
            )))
            st.rerun()
        if is_personal_loan_request(prompt) or st.session_state.get("chat_loan_active", False):
            details = dict(st.session_state.get("chat_loan_details", {}))
            details = update_personal_loan_details(
                details,
                prompt,
                configured_annual_interest_rate=CONFIGURED_ANNUAL_INTEREST_RATE,
            )
            active_loans = store.list_active_existing_loans(customer["customer_id"])
            if active_loans:
                details["existing_emi"] = store.get_existing_emi(customer["customer_id"])
            missing = missing_personal_loan_fields(details)
            st.session_state.chat_loan_details = details
            if missing:
                st.session_state.chat_loan_active = True
                messages.append(AIMessage(content=missing_fields_prompt(missing)))
                st.rerun()
            try:
                monthly_emi = calculate_emi(
                    details["loan_amount"], details["annual_interest_rate"], details["tenure_years"]
                )
                eligibility = assess_eligibility(
                    details["monthly_salary"], details["existing_emi"], monthly_emi
                )
                st.session_state.chat_loan_review = {
                    **details,
                    "customer_id": customer["customer_id"],
                    "monthly_emi": monthly_emi,
                    "eligible": eligibility.eligible,
                    "reasons": list(eligibility.reasons),
                }
                st.session_state.chat_loan_active = False
                messages.append(AIMessage(content=(
                    f"Personal-loan estimate: EMI ₹{monthly_emi:,.2f}; existing EMI "
                    f"₹{details['existing_emi']:,.2f}; combined EMI ₹{eligibility.monthly_obligation:,.2f}; "
                    f"monthly salary ₹{details['monthly_salary']:,.2f}. "
                    f"Eligibility: {'eligible under the configured rules' if eligibility.eligible else 'not eligible'}. "
                    + ("Review the summary and confirm below to submit." if eligibility.eligible else "No application can be submitted unless the checks pass. " + "; ".join(eligibility.reasons))
                )))
                st.rerun()
            except ValueError as exc:
                st.session_state.chat_loan_active = True
                messages.append(AIMessage(content=f"I couldn't calculate that request: {exc}"))
                st.rerun()
        try:
            from agent import invoke_with_free_model_fallbacks

            result = invoke_with_free_model_fallbacks(
                agent,
                customer["customer_id"],
                store,
                policy_retriever,
                {"messages": messages},
                invoke_config={"recursion_limit": 8},
            )
            st.session_state.agent_messages = result["messages"]
            for message in result["messages"]:
                if getattr(message, "name", None) != "submit_loan_application":
                    continue
                try:
                    tool_result = json.loads(message.content) if isinstance(message.content, str) else message.content
                except (TypeError, ValueError):
                    continue
                if isinstance(tool_result, dict) and tool_result.get("submitted"):
                    st.session_state.last_application = tool_result
            st.rerun()
        except Exception as exc:
            from agent import llm_error_message

            st.error(llm_error_message(exc))


def main() -> None:
    customer_id = st.session_state.get("customer_id")
    customer = store.get_customer(customer_id) if customer_id else None
    if customer is None:
        st.session_state.pop("customer_id", None)
        show_login()
        return
    header, action = st.columns([5, 1])
    with header:
        st.subheader(f"Good day, {customer['name']}")
        st.markdown('<p class="banking-subtitle">Your personal banking workspace</p>', unsafe_allow_html=True)
    with action:
        st.button("Sign out", on_click=logout, use_container_width=True)
    show_submission_success()
    account_tab, loan_tab, agent_tab, policy_tab = st.tabs(["My Account", "Personal Loan", "Banking Assistant", "Policy Library"])
    with account_tab:
        left, right = st.columns([1, 2])
        with left:
            show_account(customer)
            show_deposits(customer_id)
        with right:
            show_existing_loans(customer_id)
            show_applications(customer_id)
    with loan_tab:
        show_loan_calculator(customer_id)
    with agent_tab:
        show_chat(customer)
    with policy_tab:
        show_policy_search()
    st.caption("Simulated banking application. It does not provide real bank approval.")


if __name__ == "__main__":
    main()