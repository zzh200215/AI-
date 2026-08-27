import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from eval.bundle_utils import load_bundle_meta, resolve_eval_paths
from eval.run_eval import _normalize_expected_evidence, answer_hit, collect_badcases, run_eval


class EvalRunnerTests(unittest.TestCase):
    def test_answer_hit_ignores_whitespace_and_chinese_punctuation(self):
        self.assertTrue(answer_hit(["2026 年 8 月 1 日", "30%"], "合同约定：2026年8月1日支付30%。"))

    def test_resolve_eval_paths_prefers_bundle_dir_defaults(self):
        paths = resolve_eval_paths(bundle_dir="eval/bundles/demo")

        self.assertEqual(paths["manifest_path"], Path("eval/bundles/demo/corpus_manifest.json"))
        self.assertEqual(paths["dataset_path"], Path("eval/bundles/demo/qa_dataset.json"))
        self.assertEqual(paths["matrix_path"], Path("eval/bundles/demo/experiment_matrix.json"))
        self.assertEqual(paths["output_dir"], Path("eval/bundles/demo/outputs"))

    def test_load_bundle_meta_returns_empty_when_missing(self):
        with TemporaryDirectory() as temp_dir:
            meta = load_bundle_meta(Path(temp_dir))

        self.assertEqual(meta, {})

    def test_run_eval_computes_metrics_and_passes_config(self):
        dataset = [
            {
                "name": "answerable",
                "document_id": 1,
                "document_name": "Contract A",
                "category": "payment",
                "question": "What is the upfront payment amount?",
                "reference_answer": "The upfront payment is 1 million CNY.",
                "expected_chunk_keywords": ["1 million CNY"],
                "expected_answer_keywords": ["1 million CNY"],
                "should_refuse": False,
            },
            {
                "name": "refusal",
                "document_id": 1,
                "document_name": "Contract A",
                "category": "refusal",
                "question": "Who is the company CEO?",
                "reference_answer": "",
                "expected_chunk_keywords": [],
                "should_refuse": True,
            },
        ]

        calls = []

        def fake_answer(
            question,
            document_id=None,
            user_id=None,
            top_k=5,
            confidence_threshold=0.35,
            min_recall_candidates=None,
            recall_multiplier=None,
            query_variant_limit=None,
            context_neighbor_window=None,
            context_max_chunks=None,
        ):
            calls.append(
                {
                    "question": question,
                    "document_id": document_id,
                    "user_id": user_id,
                    "top_k": top_k,
                    "confidence_threshold": confidence_threshold,
                    "min_recall_candidates": min_recall_candidates,
                    "recall_multiplier": recall_multiplier,
                    "query_variant_limit": query_variant_limit,
                    "context_neighbor_window": context_neighbor_window,
                    "context_max_chunks": context_max_chunks,
                }
            )
            if "upfront payment" in question:
                return {
                    "answer": "The upfront payment is 1 million CNY.",
                    "citations": [{"source_text": "The contract states the upfront payment is 1 million CNY."}],
                    "hit_chunks": [{"content": "The contract states the upfront payment is 1 million CNY."}],
                    "confidence": 0.82,
                    "can_answer": True,
                }
            return {
                "answer": "The current document does not provide enough information to answer that question.",
                "citations": [],
                "hit_chunks": [],
                "confidence": 0.2,
                "can_answer": False,
            }

        with patch("eval.run_eval.agentic_rag_service.answer", side_effect=fake_answer):
            result = run_eval(dataset, user_id=7, top_k=8, confidence_threshold=0.42)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["top_k"], 8)
        self.assertEqual(calls[0]["confidence_threshold"], 0.42)
        self.assertEqual(calls[0]["user_id"], 7)
        self.assertEqual(calls[0]["min_recall_candidates"], 8)
        self.assertEqual(calls[0]["recall_multiplier"], 3)
        self.assertEqual(calls[0]["query_variant_limit"], 4)
        self.assertEqual(calls[0]["context_neighbor_window"], 1)
        self.assertEqual(calls[0]["context_max_chunks"], 8)
        self.assertEqual(result["summary"]["hit_at_k"], 1.0)
        self.assertEqual(result["summary"]["citation_accuracy"], 1.0)
        self.assertEqual(result["summary"]["refusal_accuracy"], 1.0)
        self.assertEqual(result["summary"]["answer_accuracy"], 1.0)
        self.assertEqual(result["summary"]["answer_labeled_cases"], 1)
        self.assertIsNotNone(result["summary"]["average_latency_ms"])
        self.assertEqual(result["summary"]["hit_count"], 1)
        self.assertEqual(result["summary"]["badcase_count"], 0)
        self.assertEqual(result["config"]["rag_engine"], "agentic_rag")
        self.assertEqual(result["summary"]["average_retrieval_rounds"], 0.0)
        self.assertEqual(result["config"]["top_k"], 8)
        self.assertEqual(result["config"]["confidence_threshold"], 0.42)
        self.assertEqual(result["config"]["min_recall_candidates"], 8)
        self.assertEqual(result["config"]["recall_multiplier"], 3)
        self.assertEqual(result["config"]["query_variant_limit"], 4)
        self.assertEqual(result["config"]["context_neighbor_window"], 1)
        self.assertEqual(result["config"]["context_max_chunks"], 8)
        self.assertEqual(result["cases"][0]["document_name"], "Contract A")
        self.assertEqual(result["cases"][0]["reference_answer"], "The upfront payment is 1 million CNY.")
        self.assertEqual(result["cases"][0]["case_outcome"], "pass")
        self.assertEqual(result["badcases"], [])

    def test_run_eval_includes_bundle_meta_in_config(self):
        dataset = [
            {
                "name": "answerable",
                "document_id": 1,
                "document_name": "Contract A",
                "category": "payment",
                "question": "What is the upfront payment amount?",
                "reference_answer": "The upfront payment is 1 million CNY.",
                "expected_chunk_keywords": ["1 million CNY"],
                "should_refuse": False,
            }
        ]

        with patch(
            "eval.run_eval.agentic_rag_service.answer",
            return_value={
                "answer": "The upfront payment is 1 million CNY.",
                "citations": [{"source_text": "The contract states the upfront payment is 1 million CNY."}],
                "hit_chunks": [{"content": "The contract states the upfront payment is 1 million CNY."}],
                "confidence": 0.82,
                "can_answer": True,
            },
        ):
            result = run_eval(
                dataset,
                user_id=7,
                top_k=5,
                confidence_threshold=0.35,
                bundle_meta={"bundle_name": "real_contracts_q2"},
            )

        self.assertEqual(result["config"]["bundle_meta"]["bundle_name"], "real_contracts_q2")

    def test_run_eval_can_override_runtime_config(self):
        dataset = [
            {
                "name": "answerable",
                "document_id": 1,
                "document_name": "Contract A",
                "category": "payment",
                "question": "What is the upfront payment amount?",
                "reference_answer": "The upfront payment is 1 million CNY.",
                "expected_chunk_keywords": ["1 million CNY"],
                "should_refuse": False,
            }
        ]
        calls = []

        def fake_answer(question, **kwargs):
            calls.append({"question": question, **kwargs})
            return {
                "answer": "The upfront payment is 1 million CNY.",
                "citations": [{"source_text": "The contract states the upfront payment is 1 million CNY."}],
                "hit_chunks": [{"content": "The contract states the upfront payment is 1 million CNY."}],
                "confidence": 0.82,
                "can_answer": True,
            }

        with patch("eval.run_eval.agentic_rag_service.answer", side_effect=fake_answer):
            result = run_eval(
                dataset,
                user_id=7,
                top_k=6,
                confidence_threshold=0.4,
                min_recall_candidates=20,
                recall_multiplier=4,
                query_variant_limit=2,
                context_neighbor_window=2,
                context_max_chunks=10,
            )

        self.assertEqual(calls[0]["top_k"], 6)
        self.assertEqual(calls[0]["confidence_threshold"], 0.4)
        self.assertEqual(calls[0]["min_recall_candidates"], 20)
        self.assertEqual(calls[0]["recall_multiplier"], 4)
        self.assertEqual(calls[0]["query_variant_limit"], 2)
        self.assertEqual(calls[0]["context_neighbor_window"], 2)
        self.assertEqual(calls[0]["context_max_chunks"], 10)
        self.assertEqual(result["config"]["min_recall_candidates"], 20)
        self.assertEqual(result["config"]["recall_multiplier"], 4)
        self.assertEqual(result["config"]["query_variant_limit"], 2)
        self.assertEqual(result["config"]["context_neighbor_window"], 2)
        self.assertEqual(result["config"]["context_max_chunks"], 10)

    def test_collect_badcases_filters_non_pass_cases(self):
        cases = [
            {"name": "ok", "case_outcome": "pass"},
            {"name": "bad1", "case_outcome": "retrieval_miss"},
            {"name": "bad2", "case_outcome": "false_refusal"},
        ]

        badcases = collect_badcases(cases)

        self.assertEqual([item["name"] for item in badcases], ["bad1", "bad2"])

    def test_run_eval_reports_rank_metrics_evidence_coverage_and_categories(self):
        dataset = [
            {
                "name": "multi_evidence",
                "category": "payment",
                "question": "What are the two payment milestones?",
                "expected_evidence": [
                    {"id": "advance", "keywords": ["advance", "1 million"], "match": "all"},
                    {"id": "acceptance", "keywords": ["acceptance", "2 million"], "match": "all"},
                ],
                "should_refuse": False,
            }
        ]
        response = {
            "answer": "The milestones are 1 million in advance and 2 million after acceptance.",
            "citations": [
                {"source_text": "advance payment: 1 million"},
                {"source_text": "acceptance payment: 2 million"},
            ],
            "hit_chunks": [
                {"content": "unrelated introduction"},
                {"content": "advance payment: 1 million"},
                {"content": "acceptance payment: 2 million"},
            ],
            "confidence": 0.82,
            "can_answer": True,
        }

        with patch("eval.run_eval.agentic_rag_service.answer", return_value=response):
            result = run_eval(dataset, user_id=7, top_k=5, confidence_threshold=0.35)

        self.assertEqual(result["cases"][0]["first_relevant_rank"], 2)
        self.assertEqual(result["cases"][0]["retrieval_evidence_coverage"], 1.0)
        self.assertEqual(result["summary"]["mrr"], 0.5)
        self.assertGreater(result["summary"]["ndcg_at_5"], 0.0)
        self.assertEqual(result["summary"]["retrieval_evidence_coverage"], 1.0)
        self.assertEqual(result["summary"]["citation_evidence_coverage"], 1.0)
        self.assertEqual(result["category_summary"]["payment"]["total_cases"], 1)

    def test_expected_evidence_defaults_to_original_keyword_annotation(self):
        evidence = _normalize_expected_evidence(
            {"expected_chunk_keywords": ["payment", "1 million"], "expected_chunk_match": "all"}
        )

        self.assertEqual(evidence, [{"id": "default", "keywords": ["payment", "1 million"], "match": "all"}])

    def test_run_eval_marks_citation_miss_as_badcase(self):
        dataset = [
            {
                "name": "citation_miss_case",
                "document_id": 1,
                "document_name": "Contract A",
                "category": "payment",
                "question": "What is the upfront payment amount?",
                "reference_answer": "The upfront payment is 1 million CNY.",
                "expected_chunk_keywords": ["1 million CNY"],
                "should_refuse": False,
            }
        ]

        with patch(
            "eval.run_eval.agentic_rag_service.answer",
            return_value={
                "answer": "The upfront payment is 1 million CNY.",
                "citations": [{"source_text": "The payment cycle is within 30 days."}],
                "hit_chunks": [{"content": "The contract states the upfront payment is 1 million CNY."}],
                "confidence": 0.82,
                "can_answer": True,
            },
        ):
            result = run_eval(dataset, user_id=7, top_k=5, confidence_threshold=0.35)

        self.assertEqual(result["summary"]["badcase_count"], 1)
        self.assertEqual(result["cases"][0]["case_outcome"], "citation_miss")
        self.assertEqual(result["badcases"][0]["name"], "citation_miss_case")


if __name__ == "__main__":
    unittest.main()
