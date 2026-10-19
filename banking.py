"""Deterministic loan calculations and demo eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    monthly_obligation: float
    debt_to_income_ratio: float
    reasons: tuple[str, ...]
    rules: tuple[str, ...]


MINIMUM_MONTHLY_SALARY = 25_000.0
MAX_DEBT_TO_INCOME_RATIO = 0.50
CONFIGURED_ANNUAL_INTEREST_RATE = 12.0
ELIGIBILITY_RULES = (
    f"Monthly salary must be at least ₹{MINIMUM_MONTHLY_SALARY:,.0f}.",
    f"Existing EMI plus proposed EMI must not exceed {MAX_DEBT_TO_INCOME_RATIO:.0%} of monthly salary.",
)


def _positive_number(value: float, label: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {qualifier}.")
    return number


def calculate_emi(loan_amount: float, annual_interest_rate: float, tenure_years: float) -> float:
    """Return the monthly EMI using the standard reducing-balance formula."""
    amount = _positive_number(loan_amount, "Loan amount")
    rate = _positive_number(annual_interest_rate, "Annual interest rate", allow_zero=True)
    years = _positive_number(tenure_years, "Tenure")
    months = round(years * 12)
    if months < 1 or abs(years * 12 - months) > 1e-8:
        raise ValueError("Tenure must resolve to a whole number of months.")
    monthly_rate = rate / 12 / 100
    if monthly_rate == 0:
        return round(amount / months, 2)
    factor = (1 + monthly_rate) ** months
    return round(amount * monthly_rate * factor / (factor - 1), 2)


def assess_eligibility(
    monthly_salary: float,
    existing_emi: float,
    proposed_emi: float,
) -> EligibilityResult:
    """Evaluate a loan against the app's clearly labelled fictional rules."""
    salary = _positive_number(monthly_salary, "Monthly salary")
    current_emi = _positive_number(existing_emi, "Existing EMI", allow_zero=True)
    new_emi = _positive_number(proposed_emi, "Proposed EMI", allow_zero=True)
    total = current_emi + new_emi
    ratio = total / salary
    reasons: list[str] = []
    if salary < MINIMUM_MONTHLY_SALARY:
        reasons.append(f"Monthly salary is below the configured minimum of ₹{MINIMUM_MONTHLY_SALARY:,.0f}.")
    if ratio > MAX_DEBT_TO_INCOME_RATIO:
        reasons.append("Combined monthly EMIs exceed 50% of monthly salary under the configured rules.")
    return EligibilityResult(
        eligible=not reasons,
        monthly_obligation=round(total, 2),
        debt_to_income_ratio=ratio,
        reasons=tuple(reasons),
        rules=ELIGIBILITY_RULES,
    )