import unittest

from app.services.rag.trace import TRACE_VERSION, query_summary, score_margin


class RagTraceTests(unittest.TestCase):
    def test_query_summary_does_not_keep_original_text(self):
        summary = query_summary("合同全文中的敏感条款", variant_count=3)
        self.assertEqual(summary["length"], 10)
        self.assertEqual(summary["variant_count"], 3)
        self.assertEqual(len(summary["sha256"]), 64)
        self.assertNotIn("敏感条款", summary)

    def test_score_margin_uses_top_two_retrieval_scores(self):
        self.assertEqual(
            score_margin([{"retrieval_score": 0.81}, {"retrieval_score": 0.62}]),
            0.19,
        )
        self.assertEqual(score_margin([]), 0.0)
        self.assertEqual(TRACE_VERSION, 1)
