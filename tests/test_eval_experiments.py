import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.create_eval_bundle import create_bundle
from eval.index_eval_corpus import build_chunks, load_manifest
from eval.run_experiments import build_baseline_snapshot, compare_with_baseline, run_experiments, write_experiment_outputs


class EvalCorpusTests(unittest.TestCase):
    def test_create_bundle_scaffolds_business_eval_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = create_bundle(Path(temp_dir) / "contracts_q3", "contracts_q3")
            bundle_dir = Path(result["bundle_dir"])

            self.assertTrue((bundle_dir / "bundle_meta.json").exists())
            self.assertTrue((bundle_dir / "corpus_manifest.json").exists())
            self.assertTrue((bundle_dir / "qa_dataset.json").exists())
            self.assertTrue((bundle_dir / "experiment_matrix.json").exists())
            self.assertTrue((bundle_dir / "docs" / "replace_with_real_document.md").exists())

    def test_build_chunks_preserves_document_metadata(self):
        path = Path("eval/fixtures/contract_service_agreement.md")
        chunks = build_chunks(
            document_id=9001,
            file_path=path,
            file_type="md",
            chunk_size=220,
            chunk_overlap=40,
        )

        self.assertGreater(len(chunks), 3)
        self.assertEqual(chunks[0]["embedding_id"], "doc9001_chunk0")
        self.assertIn("section_path", chunks[0])
        self.assertIn("segment_type", chunks[0])
        self.assertTrue(any("预付款" in chunk["content"] or "首付款" in chunk["content"] for chunk in chunks))

    def test_build_chunks_preserves_structure_metadata(self):
        path = Path("eval/fixtures/project_delivery_plan.md")
        chunks = build_chunks(
            document_id=9002,
            file_path=path,
            file_type="md",
            chunk_size=180,
            chunk_overlap=30,
        )

        self.assertTrue(any(chunk.get("section_path") for chunk in chunks))
        self.assertTrue(any(chunk.get("segment_type") in {"paragraph", "list"} for chunk in chunks))

    def test_load_manifest_reads_fixture_documents(self):
        manifest = load_manifest(Path("eval/corpus_manifest.json"))

        self.assertEqual(len(manifest), 3)
        self.assertEqual(manifest[0]["document_id"], 9001)


class EvalExperimentRunnerTests(unittest.TestCase):
    def test_run_experiments_indexes_and_evaluates_each_row(self):
        manifest = [{"document_id": 1, "document_name": "A", "user_id": 9, "file_path": "x", "file_type": "md"}]
        dataset = [{"question": "Q1"}]
        matrix = [
            {
                "name": "baseline",
                "chunk_size": 800,
                "chunk_overlap": 100,
                "top_k": 5,
                "confidence_threshold": 0.35,
                "context_neighbor_window": 1,
                "context_max_chunks": 8,
            },
            {
                "name": "topk_3",
                "chunk_size": 500,
                "chunk_overlap": 100,
                "top_k": 3,
                "confidence_threshold": 0.5,
                "context_neighbor_window": 2,
                "context_max_chunks": 6,
            },
        ]

        with patch("eval.run_experiments.index_corpus") as mock_index, patch("eval.run_experiments.run_eval") as mock_eval:
            mock_index.return_value = [{"document_id": 1, "chunk_count": 4}]
            mock_eval.return_value = {
                "config": {"top_k": 5, "confidence_threshold": 0.35},
                "summary": {
                    "hit_at_k": 1.0,
                    "citation_accuracy": 1.0,
                    "refusal_accuracy": 0.5,
                    "badcase_count": 1,
                },
                "badcases": [{"name": "case-1"}],
            }
            result = run_experiments(manifest=manifest, dataset=dataset, matrix=matrix, user_id=9)

        self.assertEqual(result["experiment_count"], 2)
        self.assertEqual(result["baseline_experiment"], "baseline")
        self.assertEqual(mock_index.call_count, 2)
        self.assertEqual(mock_eval.call_count, 2)
        self.assertEqual(result["results"][1]["experiment"]["name"], "topk_3")
        self.assertEqual(result["results"][0]["effective_config"]["top_k"], 5)
        self.assertEqual(mock_eval.call_args_list[0].kwargs["context_neighbor_window"], 1)
        self.assertEqual(mock_eval.call_args_list[1].kwargs["context_max_chunks"], 6)
        self.assertIn("baseline_delta", result["results"][0])
        self.assertEqual(result["results"][0]["baseline_delta"]["hit_at_k"], 0.0)

    def test_run_experiments_reuses_index_for_same_chunk_config(self):
        manifest = [{"document_id": 1, "document_name": "A", "user_id": 9, "file_path": "x", "file_type": "md"}]
        dataset = [{"question": "Q1"}]
        matrix = [
            {
                "name": "baseline",
                "chunk_size": 800,
                "chunk_overlap": 100,
                "top_k": 5,
                "confidence_threshold": 0.35,
                "context_neighbor_window": 1,
                "context_max_chunks": 8,
            },
            {
                "name": "topk_3",
                "chunk_size": 800,
                "chunk_overlap": 100,
                "top_k": 3,
                "confidence_threshold": 0.35,
                "context_neighbor_window": 1,
                "context_max_chunks": 8,
            },
        ]

        with patch("eval.run_experiments.index_corpus") as mock_index, patch("eval.run_experiments.run_eval") as mock_eval:
            mock_index.return_value = [{"document_id": 1, "chunk_count": 4}]
            mock_eval.return_value = {
                "config": {"top_k": 5, "confidence_threshold": 0.35},
                "summary": {
                    "hit_at_k": 1.0,
                    "citation_accuracy": 1.0,
                    "refusal_accuracy": 0.5,
                    "badcase_count": 0,
                },
                "badcases": [],
            }
            result = run_experiments(manifest=manifest, dataset=dataset, matrix=matrix, user_id=9)

        self.assertEqual(result["experiment_count"], 2)
        self.assertEqual(mock_index.call_count, 1)
        self.assertEqual(mock_eval.call_count, 2)

    def test_run_experiments_can_skip_indexing(self):
        manifest = [{"document_id": 1, "document_name": "A", "user_id": 9, "file_path": "x", "file_type": "md"}]
        dataset = [{"question": "Q1"}]
        matrix = [
            {
                "name": "baseline",
                "chunk_size": 800,
                "chunk_overlap": 100,
                "top_k": 5,
                "confidence_threshold": 0.35,
                "context_neighbor_window": 1,
                "context_max_chunks": 8,
            },
        ]

        with patch("eval.run_experiments.index_corpus") as mock_index, patch("eval.run_experiments.run_eval") as mock_eval:
            mock_eval.return_value = {
                "config": {"top_k": 5, "confidence_threshold": 0.35},
                "summary": {
                    "hit_at_k": 1.0,
                    "citation_accuracy": 1.0,
                    "refusal_accuracy": 0.5,
                    "badcase_count": 0,
                },
                "badcases": [],
            }
            result = run_experiments(manifest=manifest, dataset=dataset, matrix=matrix, user_id=9, skip_index=True)

        self.assertEqual(result["results"][0]["indexed_documents"], [])
        self.assertEqual(mock_index.call_count, 0)
        self.assertEqual(mock_eval.call_count, 1)

    def test_write_experiment_outputs_persists_summary_and_badcases(self):
        result = {
            "dataset_size": 3,
            "experiment_count": 1,
            "baseline_experiment": "baseline",
            "bundle_meta": {"bundle_name": "fixture"},
            "results": [
                {
                    "experiment": {"name": "baseline", "top_k": 5},
                    "effective_config": {
                        "top_k": 5,
                        "confidence_threshold": 0.35,
                        "context_neighbor_window": 1,
                        "context_max_chunks": 8,
                        "prompt_template": "rag_answer",
                        "prompt_version": 1,
                    },
                    "summary": {"hit_at_k": 1.0, "badcase_count": 1},
                    "baseline_delta": {"hit_at_k": 0.0, "badcase_count": 0},
                    "badcases": [{"name": "bad-1", "case_outcome": "citation_miss"}],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_info = write_experiment_outputs(result, Path(temp_dir))
            summary_path = Path(artifact_info["summary_path"])
            badcase_path = Path(temp_dir) / "baseline_badcases.json"
            baseline_snapshot_path = Path(artifact_info["baseline_snapshot_path"])

            self.assertTrue(summary_path.exists())
            self.assertTrue(badcase_path.exists())
            self.assertTrue(baseline_snapshot_path.exists())

            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            badcase_payload = json.loads(badcase_path.read_text(encoding="utf-8"))
            baseline_snapshot_payload = json.loads(baseline_snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(summary_payload["baseline_experiment"], "baseline")
        self.assertEqual(summary_payload["bundle_meta"]["bundle_name"], "fixture")
        self.assertEqual(summary_payload["experiments"][0]["badcase_count"], 1)
        self.assertEqual(summary_payload["experiments"][0]["effective_config"]["top_k"], 5)
        self.assertEqual(badcase_payload[0]["name"], "bad-1")
        self.assertEqual(baseline_snapshot_payload["baseline"]["effective_config"]["prompt_version"], 1)

    def test_build_baseline_snapshot_extracts_baseline_row(self):
        result = {
            "dataset_size": 5,
            "experiment_count": 2,
            "baseline_experiment": "baseline",
            "bundle_meta": {"bundle_name": "contracts_q3"},
            "results": [
                {
                    "experiment": {"name": "baseline"},
                    "effective_config": {"top_k": 5, "prompt_version": 1},
                    "summary": {"hit_at_k": 1.0, "badcase_count": 0},
                    "badcases": [],
                },
                {
                    "experiment": {"name": "topk_3"},
                    "effective_config": {"top_k": 3, "prompt_version": 1},
                    "summary": {"hit_at_k": 0.9, "badcase_count": 1},
                    "badcases": [{"name": "case-a"}],
                },
            ],
        }

        snapshot = build_baseline_snapshot(result)

        self.assertEqual(snapshot["baseline_experiment"], "baseline")
        self.assertEqual(snapshot["baseline"]["effective_config"]["top_k"], 5)
        self.assertEqual(snapshot["bundle_meta"]["bundle_name"], "contracts_q3")

    def test_compare_with_baseline_detects_metric_regression_and_config_drift(self):
        current_result = {
            "baseline_experiment": "baseline",
            "results": [
                {
                    "experiment": {"name": "baseline"},
                    "effective_config": {
                        "top_k": 5,
                        "confidence_threshold": 0.35,
                        "min_recall_candidates": 8,
                        "recall_multiplier": 3,
                        "query_variant_limit": 4,
                        "context_neighbor_window": 2,
                        "context_max_chunks": 8,
                        "prompt_template": "rag_answer",
                        "prompt_version": 2,
                    },
                    "summary": {
                        "hit_at_k": 0.95,
                        "mrr": 0.75,
                        "citation_accuracy": 0.8,
                        "refusal_accuracy": 1.0,
                        "badcase_count": 2,
                    },
                    "badcases": [{"name": "bad-1"}, {"name": "bad-2"}],
                }
            ],
        }
        baseline_snapshot = {
            "baseline_experiment": "baseline",
            "baseline": {
                "effective_config": {
                    "top_k": 5,
                    "confidence_threshold": 0.35,
                    "min_recall_candidates": 8,
                    "recall_multiplier": 3,
                    "query_variant_limit": 4,
                    "context_neighbor_window": 1,
                    "context_max_chunks": 8,
                    "prompt_template": "rag_answer",
                    "prompt_version": 1,
                },
                "summary": {
                    "hit_at_k": 1.0,
                    "mrr": 0.9,
                    "citation_accuracy": 0.9,
                    "refusal_accuracy": 1.0,
                    "badcase_count": 1,
                },
            },
        }

        comparison = compare_with_baseline(current_result, baseline_snapshot)

        self.assertTrue(comparison["regression_detected"])
        self.assertEqual(comparison["regressions"][0]["metric"], "hit_at_k")
        self.assertTrue(any(item["metric"] == "mrr" for item in comparison["regressions"]))
        self.assertTrue(any(item["metric"] == "citation_accuracy" for item in comparison["regressions"]))
        self.assertTrue(any(item["metric"] == "badcase_count" for item in comparison["regressions"]))
        self.assertTrue(any(item["field"] == "context_neighbor_window" for item in comparison["config_drift"]))
        self.assertTrue(any(item["field"] == "prompt_version" for item in comparison["config_drift"]))


if __name__ == "__main__":
    unittest.main()
