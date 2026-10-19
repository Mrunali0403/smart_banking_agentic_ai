import tempfile
import unittest
from pathlib import Path

from rag import PolicyRetriever, embed_text, format_policy_markdown


class RagTests(unittest.TestCase):
    def test_embedding_and_source_retrieval(self):
        self.assertEqual(embed_text("demo policy"), embed_text("demo policy"))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "loans.md").write_text("Salary eligibility requires monthly income. Loan EMI uses annual interest and tenure.", encoding="utf-8")
            Path(directory, "security.md").write_text("Passwords are hashed. Account details are private.", encoding="utf-8")
            retriever = PolicyRetriever(directory, chunk_words=20, overlap_words=3)
            results = retriever.retrieve("loan EMI annual interest", limit=1)
            self.assertEqual(results[0]["source"], "loans.md")
            self.assertIn("Source: loans.md", retriever.context_for("loan EMI"))

    def test_lexical_relevance_and_policy_markdown_formatting(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "loan.md").write_text("## Loan eligibility\n\n- Salary must meet the minimum.\n- Combined EMI is limited.", encoding="utf-8")
            Path(directory, "account.md").write_text("## Account access\n\nPasswords are hashed and private.", encoding="utf-8")
            result = PolicyRetriever(directory).retrieve("loan eligibility salary", limit=1)[0]
            self.assertEqual(result["source"], "loan.md")
        formatted = format_policy_markdown("# Large title\n\n## Section\n- **Salary** is required.\nSource: policy.md")
        self.assertIn("**Large title**", formatted)
        self.assertIn("**Section**", formatted)
        self.assertIn("- **Salary** is required.", formatted)
        self.assertNotIn("Source:", formatted)


if __name__ == "__main__":
    unittest.main()