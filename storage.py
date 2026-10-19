"""CSV persistence for fictional demo customers and loan applications."""

from __future__ import annotations

import csv
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from banking import assess_eligibility, calculate_emi


CUSTOMER_FIELDS = ("customer_id", "name", "email", "phone", "password_hash", "account_number", "account_type", "available_balance")
APPLICATION_STATUS = "Approved - Simulated"
APPLICATION_FIELDS = (
    "reference",
    "customer_id",
    "loan_amount",
    "annual_interest_rate",
    "tenure_years",
    "monthly_emi",
    "monthly_salary",
    "existing_emi",
    "status",
    "created_at",
)
EXISTING_LOAN_FIELDS = (
    "loan_id",
    "customer_id",
    "loan_type",
    "loan_amount",
    "status",
    "outstanding_balance",
    "monthly_emi",
)
TRANSACTION_FIELDS = (
    "transaction_id",
    "customer_id",
    "transaction_type",
    "amount",
    "balance_after",
    "description",
    "created_at",
)
ACTIVE_LOAN_STATUSES = {"active", "open", "ongoing", "disbursed", "approved", "approved - simulated"}


class DuplicateApplicationError(ValueError):
    """Raised when a recent identical application is already recorded."""


class ApplicationConfirmationError(ValueError):
    """Raised unless the customer explicitly confirms submission."""


class IneligibleApplicationError(ValueError):
    """Raised when configured eligibility rules fail."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("The application does not meet the configured eligibility rules.")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _customer_id_matches(stored_id: str | None, requested_id: str) -> bool:
    return (stored_id or "").strip().casefold() == requested_id.strip().casefold()


def _write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class BankingStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.customers_path = self.data_dir / "customers.csv"
        self.applications_path = self.data_dir / "loan_applications.csv"
        self.existing_loans_path = self.data_dir / "existing_loans.csv"
        self.transactions_path = self.data_dir / "account_transactions.csv"

    def find_customer_by_email(self, email: str) -> dict[str, str] | None:
        normalized = email.strip().lower()
        return next((row for row in _read_rows(self.customers_path) if row["email"].lower() == normalized), None)

    def get_customer(self, customer_id: str) -> dict[str, str] | None:
        return next((
            row for row in _read_rows(self.customers_path)
            if _customer_id_matches(row.get("customer_id"), customer_id)
        ), None)

    def create_customer(self, customer: dict[str, str], *, opening_deposit: float | None = None) -> dict[str, str]:
        requested_opening_balance = customer.get("available_balance", "0.00") if opening_deposit is None else opening_deposit
        deposit = self._validate_deposit(requested_opening_balance)
        customers = _read_rows(self.customers_path)
        if any(row["email"].lower() == customer["email"].lower() for row in customers):
            raise ValueError("An account with this email already exists.")
        if any(_customer_id_matches(row.get("customer_id"), customer["customer_id"]) for row in customers):
            raise ValueError("Customer ID collision. Please try again.")
        stored = {**customer, "available_balance": f"{deposit:.2f}"}
        _write_rows(self.customers_path, CUSTOMER_FIELDS, [*customers, stored])
        if deposit:
            try:
                self._append_transaction(
                    customer_id=stored["customer_id"],
                    transaction_type="Opening Deposit",
                    amount=deposit,
                    balance_after=deposit,
                    description="Opening account deposit",
                )
            except Exception:
                _write_rows(self.customers_path, CUSTOMER_FIELDS, customers)
                raise
        return stored

    @staticmethod
    def _validate_deposit(amount: float) -> float:
        try:
            value = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("Deposit amount must be a valid number.") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError("Deposit amount must be a finite, non-negative amount.")
        return round(value, 2)

    def _append_transaction(
        self,
        *,
        customer_id: str,
        transaction_type: str,
        amount: float,
        balance_after: float,
        description: str,
    ) -> dict[str, str]:
        transactions = _read_rows(self.transactions_path)
        transaction = {
            "transaction_id": f"TX-{uuid4().hex[:12].upper()}",
            "customer_id": customer_id,
            "transaction_type": transaction_type,
            "amount": f"{amount:.2f}",
            "balance_after": f"{balance_after:.2f}",
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_rows(self.transactions_path, TRANSACTION_FIELDS, [*transactions, transaction])
        return transaction

    def deposit_money(self, customer_id: str, amount: float) -> tuple[dict[str, str], dict[str, str]]:
        deposit = self._validate_deposit(amount)
        if deposit <= 0:
            raise ValueError("Deposit amount must be greater than zero.")
        customer = self.get_customer(customer_id)
        if customer is None:
            raise ValueError("The authenticated customer account was not found.")
        old_balance = float(customer["available_balance"])
        new_balance = round(old_balance + deposit, 2)
        customers = _read_rows(self.customers_path)
        updated = [
            {**row, "available_balance": f"{new_balance:.2f}"}
            if _customer_id_matches(row.get("customer_id"), customer["customer_id"])
            else row
            for row in customers
        ]
        _write_rows(self.customers_path, CUSTOMER_FIELDS, updated)
        try:
            transaction = self._append_transaction(
                customer_id=customer["customer_id"],
                transaction_type="Deposit",
                amount=deposit,
                balance_after=new_balance,
                description="Customer deposit",
            )
        except Exception:
            _write_rows(self.customers_path, CUSTOMER_FIELDS, customers)
            raise
        return self.get_customer(customer["customer_id"]), transaction

    def list_transactions(self, customer_id: str) -> list[dict[str, str]]:
        return [
            row for row in _read_rows(self.transactions_path)
            if _customer_id_matches(row.get("customer_id"), customer_id)
        ]

    def list_applications(self, customer_id: str) -> list[dict[str, str]]:
        rows = _read_rows(self.applications_path)
        return [row for row in rows if _customer_id_matches(row.get("customer_id"), customer_id)]

    def list_existing_loans(self, customer_id: str) -> list[dict[str, str]]:
        if not self.existing_loans_path.exists():
            _write_rows(self.existing_loans_path, EXISTING_LOAN_FIELDS, [])
        return [
            row
            for row in _read_rows(self.existing_loans_path)
            if _customer_id_matches(row.get("customer_id"), customer_id)
        ]

    def list_active_existing_loans(self, customer_id: str) -> list[dict[str, str]]:
        existing = [
            loan
            for loan in self.list_existing_loans(customer_id)
            if (loan.get("status") or "").strip().lower() in ACTIVE_LOAN_STATUSES
        ]
        seen_ids = {loan.get("loan_id", "").strip().casefold() for loan in existing}
        approved_applications = []
        for application in self.list_applications(customer_id):
            status = (application.get("status") or "").strip().casefold()
            reference = application.get("reference", "").strip()
            if status not in {"approved", "approved - simulated"} or reference.casefold() in seen_ids:
                continue
            approved_applications.append({
                "loan_id": reference,
                "customer_id": application["customer_id"],
                "loan_type": "Personal",
                "loan_amount": application["loan_amount"],
                "status": application["status"],
                "outstanding_balance": "",
                "monthly_emi": application["monthly_emi"],
            })
        return existing + approved_applications

    def get_existing_emi(self, customer_id: str) -> float | None:
        """Sum active-loan EMIs for this customer; return None when no complete stored value exists."""
        loans = self.list_active_existing_loans(customer_id)
        if not loans:
            return 0.0
        try:
            return round(sum(float(loan["monthly_emi"]) for loan in loans), 2)
        except (KeyError, TypeError, ValueError):
            return None

    def submit_loan_application(
        self,
        *,
        customer_id: str,
        loan_amount: float,
        annual_interest_rate: float,
        tenure_years: float,
        monthly_salary: float,
        existing_emi: float,
        confirmed: bool,
    ) -> dict[str, str]:
        if not confirmed:
            raise ApplicationConfirmationError("Confirm the application details before submitting.")
        customer = self.get_customer(customer_id)
        if customer is None:
            raise ValueError("The authenticated customer account was not found.")
        customer_id = customer["customer_id"]
        self._assert_no_recent_duplicate(customer_id, loan_amount, tenure_years)
        if self.list_active_existing_loans(customer_id):
            recorded_emi = self.get_existing_emi(customer_id)
            if recorded_emi is None:
                raise ValueError("An active loan is missing its recorded monthly EMI.")
            existing_emi = recorded_emi
        monthly_emi = calculate_emi(loan_amount, annual_interest_rate, tenure_years)
        eligibility = assess_eligibility(monthly_salary, existing_emi, monthly_emi)
        if not eligibility.eligible:
            raise IneligibleApplicationError(eligibility.reasons)
        return self.save_application({
            "customer_id": customer_id,
            "loan_amount": f"{float(loan_amount):.2f}",
            "annual_interest_rate": f"{float(annual_interest_rate):.2f}",
            "tenure_years": f"{float(tenure_years):.2f}",
            "monthly_emi": f"{monthly_emi:.2f}",
            "monthly_salary": f"{float(monthly_salary):.2f}",
            "existing_emi": f"{float(existing_emi):.2f}",
            "status": APPLICATION_STATUS,
        })

    def save_application(self, application: dict[str, str]) -> dict[str, str]:
        applications = _read_rows(self.applications_path)
        now = datetime.now(timezone.utc)
        self._assert_no_recent_duplicate(
            application["customer_id"],
            application["loan_amount"],
            application["tenure_years"],
            rows=applications,
        )
        saved = {**application, "reference": f"LA-{uuid4().hex[:10].upper()}", "created_at": now.isoformat()}
        _write_rows(self.applications_path, APPLICATION_FIELDS, [*applications, saved])
        return saved

    def _assert_no_recent_duplicate(
        self,
        customer_id: str,
        loan_amount: float | str,
        tenure_years: float | str,
        *,
        rows: list[dict[str, str]] | None = None,
    ) -> None:
        applications = _read_rows(self.applications_path) if rows is None else rows
        amount_key = f"{float(loan_amount):.2f}"
        tenure_key = f"{float(tenure_years):.2f}"
        now = datetime.now(timezone.utc)
        for row in applications:
            if (
                _customer_id_matches(row.get("customer_id"), customer_id)
                and row.get("loan_amount") == amount_key
                and row.get("tenure_years") == tenure_key
            ):
                try:
                    created = datetime.fromisoformat(row["created_at"])
                except (ValueError, TypeError, KeyError):
                    continue
                if (now - created).total_seconds() < 24 * 60 * 60:
                    raise DuplicateApplicationError(
                        f"A matching application ({row['reference']}) was submitted in the last 24 hours."
                    )