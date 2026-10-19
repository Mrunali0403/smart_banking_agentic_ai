import unittest
import tempfile

from banking import CONFIGURED_ANNUAL_INTEREST_RATE
from storage import ApplicationConfirmationError, BankingStore, DuplicateApplicationError
from loan_chat import (
    extract_personal_loan_details,
    is_unsupported_loan_application,
    is_personal_loan_request,
    is_submission_cancellation,
    is_submission_confirmation,
    missing_fields_prompt,
    missing_personal_loan_fields,
    requested_unsupported_loan,
    submit_confirmed_personal_loan,
    update_personal_loan_details,
)


class LoanChatTests(unittest.TestCase):
    def test_extracts_chat_details_and_uses_configured_rate(self):
        details = extract_personal_loan_details(
            "I want to apply for a personal loan of ₹5,00,000 for 3 years. "
            "Salary is ₹60,000 and existing EMI is ₹5,000."
        )
        self.assertEqual(details["loan_amount"], 500_000)
        self.assertEqual(details["tenure_years"], 3)
        self.assertEqual(details["monthly_salary"], 60_000)
        self.assertEqual(details["existing_emi"], 5_000)
        self.assertEqual(details["annual_interest_rate"], CONFIGURED_ANNUAL_INTEREST_RATE)
        self.assertEqual(missing_personal_loan_fields(details), [])
        self.assertTrue(is_personal_loan_request("I want to apply for a personal loan"))

    def test_extracts_explicit_rate_and_contextual_unlabeled_amount(self):
        details = extract_personal_loan_details(
            "5 lakh personal loan for 36 months at 10% annual interest, salary 60000, current EMI 5000"
        )
        self.assertEqual(details["loan_amount"], 500_000)
        self.assertEqual(details["tenure_years"], 3)
        self.assertEqual(details["annual_interest_rate"], 10)
        self.assertEqual(
            extract_personal_loan_details("personal loan at 12% for 2 years")["annual_interest_rate"],
            12,
        )
        self.assertEqual(
            extract_personal_loan_details("personal loan at 10 percent")["annual_interest_rate"],
            10,
        )
        answer = extract_personal_loan_details(
            "₹500000", allow_unlabeled_loan_amount=True, expected_fields=("loan_amount",)
        )
        self.assertEqual(answer["loan_amount"], 500_000)
        ambiguous = extract_personal_loan_details(
            "60000", expected_fields=("loan_amount", "monthly_salary", "existing_emi")
        )
        self.assertEqual(ambiguous, {"annual_interest_rate": CONFIGURED_ANNUAL_INTEREST_RATE})
        salary_answer = extract_personal_loan_details("60000", expected_fields=("monthly_salary",))
        self.assertEqual(salary_answer["monthly_salary"], 60_000)
        emi_answer = extract_personal_loan_details("5000", expected_fields=("existing_emi",))
        self.assertEqual(emi_answer["existing_emi"], 5_000)
        self.assertEqual(extract_personal_loan_details("none", expected_fields=("existing_emi",))["existing_emi"], 0)
        tenure_answer = extract_personal_loan_details("36 months", expected_fields=("tenure_years",))
        self.assertEqual(tenure_answer["tenure_years"], 3)
        salary = extract_personal_loan_details("my salary is ₹60000")
        self.assertNotIn("loan_amount", salary)

    def test_missing_values_are_reported_without_guessing_rate(self):
        details = extract_personal_loan_details(
            "Apply for a personal loan for 3 years, salary ₹60,000, EMI ₹5,000",
            default_annual_interest_rate=None,
        )
        self.assertNotIn("loan_amount", details)
        self.assertNotIn("annual_interest_rate", details)
        missing = missing_personal_loan_fields(details)
        self.assertEqual(missing, ["loan amount", "annual interest rate"])
        self.assertEqual(
            missing_fields_prompt(missing),
            "To check a personal loan, I still need: loan amount, annual interest rate. "
            "Please label each amount so I don't confuse loan principal, salary, and EMI.",
        )

    def test_multi_turn_collection_merges_only_explicit_values(self):
        details = update_personal_loan_details({}, "I want to apply for a personal loan")
        self.assertEqual(
            missing_personal_loan_fields(details),
            ["loan amount", "loan tenure", "monthly salary", "total existing monthly EMI"],
        )
        details = update_personal_loan_details(details, "₹5,00,000 for 3 years")
        self.assertEqual(missing_personal_loan_fields(details), ["monthly salary", "total existing monthly EMI"])
        details = update_personal_loan_details(details, "salary ₹60,000 and existing EMI ₹5,000")
        self.assertEqual(missing_personal_loan_fields(details), [])
        self.assertEqual(details["loan_amount"], 500_000)
        self.assertEqual(details["monthly_salary"], 60_000)

    def test_chat_does_not_assume_zero_existing_emi_when_records_are_missing(self):
        details = update_personal_loan_details({}, "I want to apply for a personal loan")
        self.assertNotIn("existing_emi", details)
        self.assertIn("total existing monthly EMI", missing_personal_loan_fields(details))
        details = update_personal_loan_details(details, "none")
        self.assertEqual(details["existing_emi"], 0)

    def test_chat_confirmation_submits_and_returns_persisted_reference(self):
        self.assertTrue(is_submission_confirmation("Yes, please submit"))
        self.assertTrue(is_submission_confirmation("Confirm the application"))
        self.assertTrue(is_submission_cancellation("Cancel"))
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            store.create_customer({
                "customer_id": "CUS-CHAT", "name": "Asha Rao", "email": "asha@example.com",
                "phone": "+91 98765 43210", "password_hash": "unused",
                "account_number": "****1234", "account_type": "Savings", "available_balance": "0",
            })
            application = {
                "customer_id": "CUS-CHAT", "loan_amount": 100_000,
                "annual_interest_rate": 12, "tenure_years": 2,
                "monthly_salary": 50_000, "existing_emi": 0,
            }
            with self.assertRaises(ApplicationConfirmationError):
                submit_confirmed_personal_loan(store, "CUS-CHAT", application, confirmed=False)
            with self.assertRaises(ValueError):
                submit_confirmed_personal_loan(store, "CUS-OTHER", application, confirmed=True)
            saved = submit_confirmed_personal_loan(store, "CUS-CHAT", application, confirmed=True)
            persisted = store.list_applications("CUS-CHAT")
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["reference"], saved["reference"])
            self.assertEqual(persisted[0]["status"], saved["status"])
            self.assertEqual(saved["status"], "Approved - Simulated")
            with self.assertRaises(DuplicateApplicationError):
                submit_confirmed_personal_loan(store, "CUS-CHAT", application, confirmed=True)

    def test_unsupported_loan_type_is_identified(self):
        self.assertEqual(requested_unsupported_loan("Can I apply for an education loan?"), "education")
        self.assertTrue(is_unsupported_loan_application("Can I apply for an education loan?"))
        self.assertFalse(is_unsupported_loan_application("What is my education loan balance?"))
        self.assertFalse(is_personal_loan_request("Can I apply for an education loan?"))

    def test_explicit_no_existing_emi_is_zero(self):
        details = extract_personal_loan_details("I have no existing EMI")
        self.assertEqual(details["existing_emi"], 0)


if __name__ == "__main__":
    unittest.main()
