"""Deterministic extraction for the personal-loan chat workflow."""

from __future__ import annotations

import re
from typing import Any

from banking import CONFIGURED_ANNUAL_INTEREST_RATE
from storage import ApplicationConfirmationError, BankingStore


_NUMBER = r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>lakhs?|lacs?|thousand|k)?"
_UNSUPPORTED_LOAN = re.compile(r"\b(education|student|home|mortgage|car|auto|vehicle|business|gold)\s+loan\b", re.IGNORECASE)
_CONFIRMATION_WORDS = {
    "yes", "y", "confirm", "confirmed", "i confirm", "i confirm submission",
    "confirm application", "confirm the application", "i confirm application",
    "i confirm the application", "yes confirm", "yes confirm application",
    "submit", "submit application", "submit the application", "yes submit",
    "yes please submit", "yes please submit the application", "confirm and submit",
    "go ahead and submit", "proceed",
}
_CANCELLATION_WORDS = {"no", "n", "cancel", "cancel application", "do not submit", "dont submit", "don't submit"}


def _amount(value: str, unit: str | None) -> float:
    number = float(value.replace(",", ""))
    normalized_unit = (unit or "").lower()
    if normalized_unit in {"lakh", "lakhs", "lac", "lacs"}:
        number *= 100_000
    elif normalized_unit in {"k", "thousand"}:
        number *= 1_000
    return number


def _money_match(pattern: str, text: str) -> float | None:
    match = re.search(pattern + _NUMBER, text, re.IGNORECASE)
    if not match:
        return None
    end = match.end()
    if re.match(r"\s*(?:years?|yrs?|months?|mos?|%)", text[end:], re.IGNORECASE):
        return None
    return _amount(match.group("value"), match.group("unit"))


def extract_personal_loan_details(
    text: str,
    *,
    default_annual_interest_rate: float | None = CONFIGURED_ANNUAL_INTEREST_RATE,
    allow_unlabeled_loan_amount: bool = False,
    expected_fields: tuple[str, ...] = (),
) -> dict[str, float]:
    """Extract explicitly stated amounts and terms; never infer omitted values."""
    details: dict[str, float] = {}
    if "existing_emi" in expected_fields and re.fullmatch(r"\s*(?:none|nil|n/?a|no|zero)\s*", text, re.IGNORECASE):
        details["existing_emi"] = 0.0
    amount_patterns = (
        r"\b(?:personal\s+)?loan(?:\s+amount)?\s*(?:(?:of|for|is)\s*|[:=]\s*)?(?:₹\s*|rs\.?\s*)?",
        r"\bborrow(?:ing)?\s*(?:(?:of|for)\s*)?(?:₹\s*|rs\.?\s*)?",
    )
    for pattern in amount_patterns:
        amount = _money_match(pattern, text)
        if amount is not None:
            details["loan_amount"] = amount
            break
    if "loan_amount" not in details:
        before_loan = re.search(_NUMBER + r"\s*(?:personal\s+)?loan\b", text, re.IGNORECASE)
        if before_loan and not re.match(r"\s*(?:years?|yrs?|months?)\b", text[before_loan.end("unit") if before_loan.group("unit") else before_loan.end("value"):], re.IGNORECASE):
            details["loan_amount"] = _amount(before_loan.group("value"), before_loan.group("unit"))
    numeric_expected = [
        field for field in expected_fields
        if field in {"loan_amount", "monthly_salary", "existing_emi"} and field not in details
    ]
    if "loan_amount" not in details and allow_unlabeled_loan_amount:
        amount_with_terms = re.search(
            r"(?:₹\s*|rs\.?\s*)" + _NUMBER
            + r"\s+(?:for\s+\d+(?:\.\d+)?\s*(?:years?|yrs?|months?|mos?)|at\s+\d+(?:\.\d+)?\s*%)",
            text,
            re.IGNORECASE,
        )
        if amount_with_terms:
            details["loan_amount"] = _amount(amount_with_terms.group("value"), amount_with_terms.group("unit"))
    if (
        "loan_amount" not in details
        and allow_unlabeled_loan_amount
        and numeric_expected == ["loan_amount"]
    ):
        amount_match = re.search(r"(?:₹\s*|rs\.?\s*)" + _NUMBER, text, re.IGNORECASE)
        if amount_match:
            details["loan_amount"] = _amount(amount_match.group("value"), amount_match.group("unit"))
    plain_amount = re.fullmatch(r"\s*(?:₹\s*|rs\.?\s*)?" + _NUMBER + r"\s*", text, re.IGNORECASE)
    if plain_amount and len(numeric_expected) == 1:
        details[numeric_expected[0]] = _amount(plain_amount.group("value"), plain_amount.group("unit"))

    tenure = re.search(r"\b(?:tenure\s*(?:(?:of|is)\s*)?|for\s+)(\d+(?:\.\d+)?)\s*(years?|yrs?|months?|mos?)\b", text, re.IGNORECASE)
    if tenure:
        years = float(tenure.group(1))
        if tenure.group(2).lower().startswith(("month", "mo")):
            years /= 12
        details["tenure_years"] = years
    elif "tenure_years" in expected_fields:
        short_tenure = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(years?|yrs?|months?|mos?)\s*", text, re.IGNORECASE)
        if short_tenure:
            years = float(short_tenure.group(1))
            if short_tenure.group(2).lower().startswith(("month", "mo")):
                years /= 12
            details["tenure_years"] = years

    salary = _money_match(r"\b(?:monthly\s+)?(?:salary|income)\s*(?:(?:is|of|around)\s*|[:=]\s*)?(?:₹\s*|rs\.?\s*)?", text)
    if salary is None:
        salary = _money_match(r"\bearn(?:s|ing)?\s*(?:₹\s*|rs\.?\s*)?", text)
    if salary is not None:
        details["monthly_salary"] = salary

    existing_emi = _money_match(
        r"\b(?:(?:existing|current|other|another)\s+)?(?:monthly\s+)?emi\s*(?:(?:is|of|around)\s*|[:=]\s*)?(?:₹\s*|rs\.?\s*)?",
        text,
    )
    if existing_emi is None:
        paid_emi = re.search(
            r"\bpay(?:ing)?\s*(?:₹\s*|rs\.?\s*)?" + _NUMBER + r"\s+(?:as\s+)?(?:existing\s+)?emi\b",
            text,
            re.IGNORECASE,
        )
        if paid_emi:
            existing_emi = _amount(paid_emi.group("value"), paid_emi.group("unit"))
    if existing_emi is None and re.search(r"\b(?:no|zero|none)\s+(?:other\s+|existing\s+|current\s+)?(?:monthly\s+)?emi\b", text, re.IGNORECASE):
        existing_emi = 0.0
    if existing_emi is not None:
        details["existing_emi"] = existing_emi

    interest = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:%|percent|per\s+cent)\s*(?:(?:p\.?a\.?|annual|per\s+annum)\b)?",
        text,
        re.IGNORECASE,
    )
    if interest:
        details["annual_interest_rate"] = float(interest.group(1))
    elif default_annual_interest_rate is not None:
        details["annual_interest_rate"] = default_annual_interest_rate
    return details


def missing_personal_loan_fields(details: dict[str, Any]) -> list[str]:
    labels = {
        "loan_amount": "loan amount",
        "tenure_years": "loan tenure",
        "monthly_salary": "monthly salary",
        "existing_emi": "total existing monthly EMI",
        "annual_interest_rate": "annual interest rate",
    }
    return [label for field, label in labels.items() if details.get(field) is None]


def update_personal_loan_details(
    previous: dict[str, Any],
    message: str,
    *,
    configured_annual_interest_rate: float | None = CONFIGURED_ANNUAL_INTEREST_RATE,
) -> dict[str, Any]:
    details = dict(previous)
    missing = missing_personal_loan_fields(details)
    expected_fields = tuple(
        field
        for field, label in (
            ("loan_amount", "loan amount"),
            ("tenure_years", "loan tenure"),
            ("monthly_salary", "monthly salary"),
            ("existing_emi", "total existing monthly EMI"),
            ("annual_interest_rate", "annual interest rate"),
        )
        if label in missing
    )
    extracted = extract_personal_loan_details(
        message,
        default_annual_interest_rate=(
            None if "annual_interest_rate" in details else configured_annual_interest_rate
        ),
        allow_unlabeled_loan_amount="loan amount" in missing,
        expected_fields=expected_fields,
    )
    details.update(extracted)
    if configured_annual_interest_rate is not None:
        details.setdefault("annual_interest_rate", configured_annual_interest_rate)
    return details


def missing_fields_prompt(missing: list[str]) -> str:
    if not missing:
        return ""
    return (
        "To check a personal loan, I still need: " + ", ".join(missing)
        + ". Please label each amount so I don't confuse loan principal, salary, and EMI."
    )


def is_submission_confirmation(text: str) -> bool:
    normalized = re.sub(r"[^a-z']+", " ", text.lower()).strip()
    return normalized in _CONFIRMATION_WORDS


def is_submission_cancellation(text: str) -> bool:
    normalized = re.sub(r"[^a-z']+", " ", text.lower()).strip()
    return normalized in _CANCELLATION_WORDS


def submit_confirmed_personal_loan(
    store: BankingStore,
    customer_id: str,
    application: dict[str, Any],
    *,
    confirmed: bool,
) -> dict[str, str]:
    if not confirmed:
        raise ApplicationConfirmationError("Confirm the application in chat before submitting.")
    if application.get("customer_id") != customer_id:
        raise ValueError("The application does not belong to the authenticated customer.")
    return store.submit_loan_application(
        customer_id=customer_id,
        loan_amount=application["loan_amount"],
        annual_interest_rate=application["annual_interest_rate"],
        tenure_years=application["tenure_years"],
        monthly_salary=application["monthly_salary"],
        existing_emi=application["existing_emi"],
        confirmed=True,
    )


def is_personal_loan_request(text: str) -> bool:
    if _UNSUPPORTED_LOAN.search(text):
        return False
    has_personal_loan = re.search(r"\bpersonal\s+loan\b", text, re.IGNORECASE)
    has_intent = re.search(r"\b(apply|application|borrow|take|request|need|want|get)\b", text, re.IGNORECASE)
    return bool(has_personal_loan and has_intent)


def requested_unsupported_loan(text: str) -> str | None:
    match = _UNSUPPORTED_LOAN.search(text)
    return match.group(1).lower() if match else None


def is_unsupported_loan_application(text: str) -> bool:
    return bool(
        requested_unsupported_loan(text)
        and re.search(r"\b(apply|application|borrow|take|request|need|want|get)\b", text, re.IGNORECASE)
    )