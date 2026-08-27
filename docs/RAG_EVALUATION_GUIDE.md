# RAG Evaluation Guide

This project evaluates the production `agentic_rag_service.answer()` path. The
goal is to make retrieval and answer changes comparable, not to use a model
judge as the only source of truth.

## What is evaluated

`eval/run_eval.py` reports four layers of quality:

| Layer | Metrics | Meaning |
| --- | --- | --- |
| Retrieval | `hit_at_k`, `mrr`, `ndcg_at_K`, `retrieval_evidence_coverage` | Whether gold evidence was found, ranked early, and found completely. |
| Citation | `citation_accuracy`, `citation_evidence_coverage` | Whether the answer cites the evidence that was expected. |
| Answer/refusal | `answer_accuracy`, `refusal_accuracy` | Whether labelled answer terms are present and unsupported questions are refused. |
| Engineering | latency average/P50/P95, retrieval rounds, badcase count | Whether quality changes add unacceptable cost or instability. |

`hit_at_k` remains intentionally lenient: one gold evidence item in top K is a
hit. `retrieval_evidence_coverage` and `ndcg_at_K` should be used for questions
that require several clauses or facts.

## Dataset format

The legacy format remains supported:

```json
{
  "name": "contract_amount",
  "category": "contract_fact",
  "question": "What is the contract amount?",
  "expected_chunk_keywords": ["268万元"],
  "expected_answer_keywords": ["268万元"],
  "should_refuse": false
}
```

For a multi-fact answer, use `expected_evidence`. Each entry represents one
gold passage or fact. `match: "all"` prevents a partial keyword match from
being labelled as that evidence item.

```json
{
  "name": "payment_milestones",
  "category": "contract_fact",
  "question": "What are the first two payment milestones?",
  "expected_evidence": [
    {"id": "advance", "keywords": ["首付款", "100万元"], "match": "all"},
    {"id": "acceptance", "keywords": ["验收", "108万元"], "match": "all"}
  ],
  "expected_answer_keywords": ["100万元", "108万元"],
  "should_refuse": false
}
```

Use `should_refuse: true` only when the correct result is to refuse because the
corpus does not provide a basis. Questions where the corpus provides a rule but
the user omitted facts should remain answerable and expect the answer to ask
for those facts.

## Runbook

1. Create a separate bundle rather than overwriting the demo data.

```powershell
python eval/create_eval_bundle.py --bundle-name legal_q3
```

2. Add desensitized documents in `eval/bundles/legal_q3/docs/`, then annotate
the corpus manifest and QA dataset. Validate before invoking models.

```powershell
python eval/run_eval.py --bundle-dir eval/bundles/legal_q3 --validate-only --pretty
```

3. Build the vector index and run the baseline.

```powershell
python eval/index_eval_corpus.py --bundle-dir eval/bundles/legal_q3 --pretty
python eval/run_experiments.py --bundle-dir eval/bundles/legal_q3 --user-id 9000 --write-artifacts --pretty
```

4. Review `outputs/summary.json` and each `*_badcases.json`. Do not change the
baseline until failures are understood. After accepting a baseline, compare a
candidate configuration with it.

```powershell
python eval/run_experiments.py --bundle-dir eval/bundles/legal_q3 --user-id 9000 --check-regression --baseline-path eval/bundles/legal_q3/outputs/baseline_snapshot.json --pretty
```

The command exits with code `2` when a tracked quality metric drops or when the
badcase count increases.

## Annotation rules

- Start with 30 to 50 questions, balanced across contract facts, contract risks,
  legal consultation, missing-fact prompts, and refusal cases.
- Keep 20% to 30% of cases for refusal or insufficient-evidence behaviour.
- Give every case a stable `name` and `category`, so `category_summary` can
  locate regressions.
- Add a failed online case only after desensitization and human review; do not
  silently modify its expected answer to make the current model pass.
- Record the dataset fingerprint, prompt version, RAG parameters, and corpus
  version alongside every accepted baseline.

## Interpreting failures

| Badcase | First check |
| --- | --- |
| `retrieval_miss` | Chunking, metadata filters, query rewrite, dense/BM25 recall, then reranker. |
| `citation_miss` | Whether the relevant chunk reached context and whether citations were truncated. |
| `false_refusal` | Confidence threshold and evidence coverage, not only model wording. |
| `missed_refusal` | Unsupported corpus answers, grounding policy, and refusal threshold. |

LLM-as-a-judge is optional (`--llm-judge`) and should be used as a review
signal for nuanced answers, not as the regression gate.
