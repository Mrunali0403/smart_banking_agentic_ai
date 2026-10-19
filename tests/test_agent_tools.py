import csv
import tempfile
import unittest

from agent import get_existing_loan_tool_result
from storage import BankingStore


class ExistingLoanToolTests(unittest.TestCase):
    def test_no_loans_returns_clear_tool_message(self):
        with tempfile.TemporaryDirectory() as directory:
            result = get_existing_loan_tool_result(BankingStore(directory), "CUS-EMPTY")
        self.assertFalse(result["found"])
        self.assertEqual(result["loans"], [])
        self.assertIn("No existing loans", result["message"])

    def test_tool_result_contains_only_authenticated_customers_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            store.existing_loans_path.parent.mkdir(parents=True, exist_ok=True)
            fields = ("loan_id", "customer_id", "loan_type", "loan_amount", "status", "outstanding_balance", "monthly_emi")
            with store.existing_loans_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {"loan_id": "LN-OWN", "customer_id": "CUS-OWN", "loan_type": "Personal", "loan_amount": "100000", "status": "Active", "outstanding_balance": "50000", "monthly_emi": "4000"},
                    {"loan_id": "LN-OTHER", "customer_id": "CUS-OTHER", "loan_type": "Personal", "loan_amount": "200000", "status": "Active", "outstanding_balance": "90000", "monthly_emi": "6000"},
                ])
            result = get_existing_loan_tool_result(store, "CUS-OWN")
        self.assertTrue(result["found"])
        self.assertEqual([loan["loan_id"] for loan in result["loans"]], ["LN-OWN"])


if __name__ == "__main__":
    unittest.main()
