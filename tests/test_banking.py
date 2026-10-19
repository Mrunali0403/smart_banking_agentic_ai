import unittest

from banking import assess_eligibility, calculate_emi


class BankingTests(unittest.TestCase):
    def test_sample_loan_emi_and_eligibility(self):
        emi = calculate_emi(500_000, 12, 3)
        self.assertAlmostEqual(emi, 16_607.15, places=2)
        result = assess_eligibility(60_000, 5_000, emi)
        self.assertTrue(result.eligible)
        self.assertAlmostEqual(result.monthly_obligation, 21_607.15, places=2)

    def test_zero_interest_emi(self):
        self.assertEqual(calculate_emi(120_000, 0, 1), 10_000)

    def test_debt_ratio_rejects_application(self):
        result = assess_eligibility(30_000, 5_000, 10_001)
        self.assertFalse(result.eligible)
        self.assertTrue(result.reasons)

    def test_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            calculate_emi(-1, 12, 3)


if __name__ == "__main__":
    unittest.main()