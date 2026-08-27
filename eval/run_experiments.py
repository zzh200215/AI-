import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eval.common import ensure_eval_llm_ready, set_eval_seed
from eval.bundle_utils import (
    DEFAULT_BASELINE_SNAPSHOT_PATH,
    DEFAULT_DATASET_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_MATRIX_PATH,
    DEFAULT_OUTPUT_DIR,
    load_bundle_meta,
    resolve_eval_paths,
)
from eval.index_eval_corpus import index_corpus, load_manifest
from eval.run_eval import load_dataset, run_eval

def load_matrix(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("Experiment matrix must be a list")
    return data


def _metric_delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return round(candidate - baseline, 4)


def _build_summary_delta(candidate_summary: dict, baseline_summary: dict | None) -> dict:
    if not baseline_summary:
        return {}
    tracked_metrics = (
        "hit_at_k",
        "mrr",
        "citation_accuracy",
        "refusal_accuracy",
        "answer_accuracy",
        "retrieval_evidence_coverage",
        "citation_evidence_coverage",
        "badcase_count",
    )
    delta = {}
    for metric in tracked_metrics:
        candidate_value = candidate_summary.get(metric)
        baseline_value = baseline_summary.get(metric)
        if metric == "badcase_count" and candidate_value is not None and baseline_value is not None:
            delta[metric] = int(candidate_value) - int(baseline_value)
            continue
        delta[metric] = _metric_delta(candidate_value, baseline_value)
    return delta


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def build_baseline_snapshot(result: dict) -> dict:
    baseline_name = result.get("baseline_experiment")
    baseline_row = None
    for row in result.get("results", []):
        experiment = row.get("experiment") or {}
        if (experiment.get("name") or "baseline") == baseline_name:
            baseline_row = row
            break
    if not baseline_row:
        raise ValueError("Baseline experiment result not found")
    return {
        "baseline_experiment": baseline_name,
        "dataset_size": result.get("dataset_size"),
        "experiment_count": result.get("experiment_count"),
        "bundle_meta": result.get("bundle_meta") or {},
        "baseline": {
            "experiment": baseline_row.get("experiment") or {},
            "effective_config": baseline_row.get("effective_config") or {},
            "summary": baseline_row.get("summary") or {},
            "badcase_count": len(baseline_row.get("badcases") or []),
        },
    }


def compare_with_baseline(current_result: dict, baseline_snapshot: dict) -> dict:
    baseline_name = baseline_snapshot.get("baseline_experiment") or current_result.get("baseline_experiment")
    current_row = None
    for row in current_result.get("results", []):
        experiment = row.get("experiment") or {}
        if (experiment.get("name") or "baseline") == baseline_name:
            current_row = row
            break
    if not current_row:
        raise ValueError("Current baseline experiment result not found")

    current_config = current_row.get("effective_config") or {}
    current_summary = current_row.get("summary") or {}
    baseline_payload = baseline_snapshot.get("baseline") or {}
    baseline_config = baseline_payload.get("effective_config") or {}
    baseline_summary = baseline_payload.get("summary") or {}

    regressions = []
    for metric in (
        "hit_at_k",
        "mrr",
        "citation_accuracy",
        "refusal_accuracy",
        "answer_accuracy",
        "retrieval_evidence_coverage",
        "citation_evidence_coverage",
    ):
        current_value = current_summary.get(metric)
        baseline_value = baseline_summary.get(metric)
        if current_value is None or baseline_value is None:
            continue
        if current_value < baseline_value:
            regressions.append(
                {
                    "metric": metric,
                    "baseline": baseline_value,
                    "current": current_value,
                    "delta": round(current_value - baseline_value, 4),
                }
            )
    if (
        current_summary.get("badcase_count") is not None
        and baseline_summary.get("badcase_count") is not None
        and int(current_summary["badcase_count"]) > int(baseline_summary["badcase_count"])
    ):
        regressions.append(
            {
                "metric": "badcase_count",
                "baseline": int(baseline_summary["badcase_count"]),
                "current": int(current_summary["badcase_count"]),
                "delta": int(current_summary["badcase_count"]) - int(baseline_summary["badcase_count"]),
            }
        )

    config_drift = []
    for field in (
        "top_k",
        "confidence_threshold",
        "min_recall_candidates",
        "recall_multiplier",
        "query_variant_limit",
        "context_neighbor_window",
        "context_max_chunks",
        "prompt_template",
        "prompt_version",
        "rag_engine",
    ):
        if current_config.get(field) != baseline_config.get(field):
            config_drift.append(
                {
                    "field": field,
                    "baseline": baseline_config.get(field),
                    "current": current_config.get(field),
                }
            )

    return {
        "baseline_experiment": baseline_name,
        "regression_detected": bool(regressions),
        "regressions": regressions,
        "config_drift": config_drift,
        "current_summary": current_summary,
        "baseline_summary": baseline_summary,
    }


def write_experiment_outputs(result: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    for row in result.get("results", []):
        experiment = row.get("experiment") or {}
        experiment_name = experiment.get("name") or "unnamed"
        badcases = row.get("badcases") or []
        badcase_path = output_dir / f"{experiment_name}_badcases.json"
        _write_json(badcase_path, badcases)
        summary_rows.append(
            {
                "name": experiment_name,
                "effective_config": row.get("effective_config") or {},
                "summary": row.get("summary") or {},
                "baseline_delta": row.get("baseline_delta") or {},
                "badcase_count": len(badcases),
                "badcase_path": str(badcase_path),
            }
        )

    summary_path = output_dir / "summary.json"
    baseline_snapshot_path = output_dir / "baseline_snapshot.json"
    _write_json(
        summary_path,
        {
            "dataset_size": result.get("dataset_size"),
            "experiment_count": result.get("experiment_count"),
            "baseline_experiment": result.get("baseline_experiment"),
            "bundle_meta": result.get("bundle_meta") or {},
            "experiments": summary_rows,
        },
    )
    _write_json(baseline_snapshot_path, build_baseline_snapshot(result))
    return {
        "output_dir": str(output_dir),
        "summary_path": str(summary_path),
        "baseline_snapshot_path": str(baseline_snapshot_path),
    }


def run_experiments(
    *,
    manifest: list[dict],
    dataset: list[dict],
    matrix: list[dict],
    user_id: int | None,
    skip_index: bool = False,
    bundle_meta: dict | None = None,
) -> dict:
    results = []
    indexed_cache: dict[tuple[int, int], list[dict]] = {}
    baseline_summary: dict | None = None
    baseline_experiment_name: str | None = None
    for experiment in matrix:
        cache_key = (experiment["chunk_size"], experiment["chunk_overlap"])
        if skip_index:
            indexed_documents = []
        else:
            if cache_key not in indexed_cache:
                indexed_cache[cache_key] = index_corpus(
                    manifest,
                    chunk_size=experiment["chunk_size"],
                    chunk_overlap=experiment["chunk_overlap"],
                )
            indexed_documents = indexed_cache[cache_key]
        evaluation = run_eval(
            dataset,
            user_id=user_id,
            top_k=experiment["top_k"],
            confidence_threshold=experiment["confidence_threshold"],
            min_recall_candidates=experiment.get("min_recall_candidates"),
            recall_multiplier=experiment.get("recall_multiplier"),
            query_variant_limit=experiment.get("query_variant_limit"),
            context_neighbor_window=experiment.get("context_neighbor_window"),
            context_max_chunks=experiment.get("context_max_chunks"),
            bundle_meta=bundle_meta,
        )
        if baseline_summary is None:
            baseline_summary = evaluation["summary"]
            baseline_experiment_name = experiment.get("name") or "baseline"
        results.append(
            {
                "experiment": experiment,
                "effective_config": evaluation.get("config") or {},
                "indexed_documents": indexed_documents,
                "summary": evaluation["summary"],
                "baseline_delta": _build_summary_delta(evaluation["summary"], baseline_summary),
                "badcases": evaluation.get("badcases", []),
            }
        )
    return {
        "dataset_size": len(dataset),
        "experiment_count": len(results),
        "baseline_experiment": baseline_experiment_name,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run batch RAG experiments over eval fixtures")
    parser.add_argument("--bundle-dir", default=None, help="Directory of an eval bundle containing manifest/dataset/matrix")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to corpus manifest json")
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH), help="Path to experiment matrix json")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="Path to qa dataset json")
    parser.add_argument("--user-id", type=int, default=9000, help="User id filter for RAG evaluation")
    parser.add_argument("--experiment", default=None, help="Optional single experiment name to run")
    parser.add_argument("--skip-index", action="store_true", help="Use existing Chroma index and skip re-indexing corpus")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible evaluation")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for summary and badcase artifacts")
    parser.add_argument(
        "--baseline-path",
        default=str(DEFAULT_BASELINE_SNAPSHOT_PATH),
        help="Path of the baseline snapshot used for regression checks",
    )
    parser.add_argument("--write-artifacts", action="store_true", help="Write summary and per-experiment badcase json files")
    parser.add_argument("--check-regression", action="store_true", help="Compare current baseline result with a saved baseline snapshot")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print result json")
    return parser.parse_args()


def main():
    args = parse_args()
    set_eval_seed(args.seed)  # 阶段 4：固定随机种子，保证同版本可复现
    ensure_eval_llm_ready()
    paths = resolve_eval_paths(
        bundle_dir=args.bundle_dir,
        manifest_path=args.manifest,
        dataset_path=args.dataset,
        matrix_path=args.matrix,
        output_dir=args.output_dir,
    )
    manifest = load_manifest(paths["manifest_path"])
    matrix = load_matrix(paths["matrix_path"])
    if args.experiment:
        matrix = [item for item in matrix if item.get("name") == args.experiment]
        if not matrix:
            raise ValueError(f"Experiment not found: {args.experiment}")
    dataset = load_dataset(paths["dataset_path"])
    bundle_meta = load_bundle_meta(paths["bundle_dir"])
    baseline_path = Path(args.baseline_path)
    if args.baseline_path == str(DEFAULT_BASELINE_SNAPSHOT_PATH) and paths["bundle_dir"] is not None:
        baseline_path = paths["output_dir"] / "baseline_snapshot.json"
    baseline_snapshot = None
    if args.check_regression:
        if not baseline_path.exists():
            raise ValueError(f"Baseline snapshot not found: {baseline_path}")
        with baseline_path.open("r", encoding="utf-8") as file:
            baseline_snapshot = json.load(file)
    results = run_experiments(
        manifest=manifest,
        dataset=dataset,
        matrix=matrix,
        user_id=args.user_id,
        skip_index=args.skip_index,
        bundle_meta=bundle_meta,
    )
    results["bundle_meta"] = bundle_meta
    if args.write_artifacts:
        artifact_info = write_experiment_outputs(results, paths["output_dir"])
        results["artifacts"] = artifact_info
    if args.check_regression:
        results["regression_check"] = compare_with_baseline(results, baseline_snapshot)
    if args.pretty:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(results, ensure_ascii=False))
    if results.get("regression_check", {}).get("regression_detected"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
