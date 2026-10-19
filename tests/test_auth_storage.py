import csv
import tempfile
import unittest
from pathlib import Path

from auth import hash_password, validate_registration, verify_password
from storage import (
    APPLICATION_STATUS,
    ApplicationConfirmationError,
    BankingStore,
    DuplicateApplicationError,
    IneligibleApplicationError,
)


class AuthStorageTests(unittest.TestCase):
    def test_password_hash_and_registration_validation(self):
        encoded = hash_password("correct horse battery")
        self.assertTrue(verify_password("correct horse battery", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertEqual(validate_registration("  Asha   Rao ", "ASHA@example.com", "+91 98765 43210", "password1")[0], "Asha Rao")
        with self.assertRaises(ValueError):
            validate_registration("A", "bad", "123", "short")

    def test_customer_storage_and_application_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            customer = {
                "customer_id": "CUS-123", "name": "Asha Rao", "email": "asha@example.com",
                "phone": "+91 98765 43210", "password_hash": "scrypt$fake",
                "account_number": "****1234", "account_type": "Demo savings", "available_balance": "0.00",
            }
            store.create_customer(customer)
            self.assertEqual(store.find_customer_by_email("ASHA@example.com")["customer_id"], "CUS-123")
            with self.assertRaises(ValueError):
                store.create_customer(customer)
            application = {
                "customer_id": "CUS-123", "loan_amount": "500000.00", "annual_interest_rate": "12.00",
                "tenure_years": "3.00", "monthly_emi": "16607.15", "monthly_salary": "60000.00",
                "existing_emi": "5000.00", "status": "Submitted - Pending Review",
            }
            saved = store.save_application(application)
            self.assertTrue(saved["reference"].startswith("LA-"))
            self.assertEqual(len(store.list_applications("CUS-123")), 1)
            with self.assertRaises(DuplicateApplicationError):
                store.save_application(application)
            self.assertTrue(Path(directory, "customers.csv").exists())

    def test_submission_requires_confirmation_and_passes_python_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            store.create_customer({
                "customer_id": "CUS-123", "name": "Asha Rao", "email": "asha@example.com",
                "phone": "+91 98765 43210", "password_hash": "scrypt$fake",
                "account_number": "****1234", "account_type": "Savings Account", "available_balance": "0.00",
            })
            application = {
                "customer_id": "CUS-123", "loan_amount": 500_000, "annual_interest_rate": 12,
                "tenure_years": 3, "monthly_salary": 60_000, "existing_emi": 5_000,
            }
            with self.assertRaises(ApplicationConfirmationError):
                store.submit_loan_application(**application, confirmed=False)
            with self.assertRaises(IneligibleApplicationError):
                store.submit_loan_application(**{**application, "monthly_salary": 30_000}, confirmed=True)
            self.assertEqual(store.list_applications("CUS-123"), [])
            saved = store.submit_loan_application(**application, confirmed=True)
            self.assertEqual(saved["status"], APPLICATION_STATUS)
            self.assertEqual(saved["monthly_emi"], "16607.15")
            stored = store.list_applications("CUS-123")
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["reference"], saved["reference"])
            self.assertEqual(stored[0]["status"], saved["status"])
            with self.assertRaises(DuplicateApplicationError):
                store.submit_loan_application(**application, confirmed=True)

    def test_existing_loans_are_customer_scoped_and_created_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            self.assertEqual(store.list_existing_loans("CUS-123"), [])
            self.assertEqual(
                store.existing_loans_path.read_text(encoding="utf-8").strip(),
                "loan_id,customer_id,loan_type,loan_amount,status,outstanding_balance,monthly_emi",
            )
            self.assertEqual(store.get_existing_emi("CUS-123"), 0)
            records = [
                {
                    "loan_id": "LN-001", "customer_id": "CUS-123", "loan_type": "Personal",
                    "loan_amount": "150000.00", "status": "Active", "outstanding_balance": "72000.00",
                    "monthly_emi": "5500.00",
                },
                {
                    "loan_id": "LN-002", "customer_id": "CUS-OTHER", "loan_type": "Education",
                    "loan_amount": "300000.00", "status": "Approved", "outstanding_balance": "250000.00",
                    "monthly_emi": "7500.00",
                },
            ]
            with store.existing_loans_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
            customer_loans = store.list_existing_loans("CUS-123")
            self.assertEqual([loan["loan_id"] for loan in customer_loans], ["LN-001"])
            self.assertEqual(customer_loans[0]["outstanding_balance"], "72000.00")
            self.assertEqual(store.list_existing_loans("CUS-OTHER")[0]["loan_id"], "LN-002")
            self.assertEqual(store.get_existing_emi("CUS-123"), 5_500)
            self.assertEqual(store.get_existing_emi("CUS-OTHER"), 7_500)

    def test_submission_uses_stored_active_emi_and_persists_returned_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            store.create_customer({
                "customer_id": "CUS-123", "name": "Asha Rao", "email": "asha@example.com",
                "phone": "+91 98765 43210", "password_hash": "scrypt$fake",
                "account_number": "****1234", "account_type": "Savings Account", "available_balance": "0.00",
            })
            store.existing_loans_path.write_text(
                "loan_id,customer_id,loan_type,loan_amount,status,outstanding_balance,monthly_emi\n"
                "LN-001,CUS-123,Personal,150000,Active,72000,5500\n",
                encoding="utf-8",
            )
            saved = store.submit_loan_application(
                customer_id="CUS-123",
                loan_amount=300_000,
                annual_interest_rate=12,
                tenure_years=3,
                monthly_salary=60_000,
                existing_emi=0,
                confirmed=True,
            )
            persisted = store.list_applications("CUS-123")
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["reference"], saved["reference"])
            self.assertEqual(persisted[0]["status"], "Approved - Simulated")
            self.assertEqual(float(persisted[0]["existing_emi"]), 5_500)

    def test_account_balance_active_emi_pending_and_submission_share_customer_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            store.create_customer({
                "customer_id": "CUS-123", "name": "Test Customer", "email": "test@example.com",
                "phone": "+919876543210", "password_hash": "unused",
                "account_number": "****1234", "account_type": "Savings Account",
                "available_balance": "18425.75",
            })
            customer = store.get_customer(" cus-123 ")
            self.assertEqual(customer["available_balance"], "18425.75")

            store.existing_loans_path.write_text(
                "loan_id,customer_id,loan_type,loan_amount,status,outstanding_balance,monthly_emi\n"
                "LN-1, cus-123 ,Personal,120000,Active,60000,4500\n"
                "LN-2,CUS-123,Education,80000,Approved,40000,1500\n"
                "LN-3,CUS-123,Personal,50000,Pending,50000,2000\n"
                "LN-4,CUS-123,Personal,40000,Rejected,40000,1200\n"
                "LN-5,CUS-123,Personal,30000,Closed,0,900\n"
                "LN-6,CUS-OTHER,Personal,200000,Active,100000,6000\n",
                encoding="utf-8",
            )
            self.assertEqual(store.get_existing_emi(customer["customer_id"]), 6_000)
            self.assertEqual(len(store.list_active_existing_loans("CUS-123")), 2)
            self.assertEqual(store.get_existing_emi("CUS-OTHER"), 6_000)

            pending = store.save_application({
                "customer_id": "cus-123", "loan_amount": "250000.00", "annual_interest_rate": "12.00",
                "tenure_years": "3.00", "monthly_emi": "8303.58", "monthly_salary": "60000.00",
                "existing_emi": "4500.00", "status": "Submitted - Pending Review",
            })
            self.assertEqual(store.list_applications("CUS-123")[0]["reference"], pending["reference"])
            self.assertEqual(len(store.list_existing_loans("CUS-123")), 5)
            self.assertEqual(len(store.list_active_existing_loans("CUS-123")), 2)

            saved = store.submit_loan_application(
                customer_id=" cus-123 ", loan_amount=100_000, annual_interest_rate=12,
                tenure_years=2, monthly_salary=60_000, existing_emi=0, confirmed=True,
            )
            self.assertEqual(saved["customer_id"], "CUS-123")
            self.assertEqual(float(saved["existing_emi"]), 6_000)
            self.assertEqual(store.get_customer("CUS-123")["available_balance"], "18425.75")
            self.assertEqual(
                [row["reference"] for row in store.list_applications("CUS-123")],
                [pending["reference"], saved["reference"]],
            )
            self.assertEqual(len(store.list_active_existing_loans("CUS-123")), 3)
            self.assertAlmostEqual(
                store.get_existing_emi("CUS-123"),
                6_000 + float(saved["monthly_emi"]),
            )

    def test_opening_deposit_and_deposits_update_balance_and_owner_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BankingStore(directory)
            opened = store.create_customer({
                "customer_id": "CUS-123", "name": "Test Customer", "email": "test@example.com",
                "phone": "+919876543210", "password_hash": "unused",
                "account_number": "****1234", "account_type": "Savings Account",
            }, opening_deposit=1_250.50)
            self.assertEqual(opened["available_balance"], "1250.50")
            self.assertEqual(len(store.list_transactions("CUS-123")), 1)
            self.assertEqual(store.list_transactions("CUS-123")[0]["transaction_type"], "Opening Deposit")

            updated, transaction = store.deposit_money(" cus-123 ", 500)
            self.assertEqual(updated["available_balance"], "1750.50")
            self.assertEqual(transaction["transaction_type"], "Deposit")
            self.assertEqual(transaction["balance_after"], "1750.50")
            self.assertEqual(len(store.list_transactions("CUS-123")), 2)
            self.assertEqual(store.list_transactions("CUS-OTHER"), [])
            with self.assertRaises(ValueError):
                store.deposit_money("CUS-123", 0)


if __name__ == "__main__":
    unittest.main()