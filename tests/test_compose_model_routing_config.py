from pathlib import Path
import re

ROUTING_KEYS = {
    "LLM_MODEL_ROUTING_ENABLED",
    "LLM_SMALL_MODEL",
    "LLM_SMALL_MODEL_PROVIDER",
    "LLM_SMALL_MODEL_API_BASE_URL",
    "LLM_SMALL_MODEL_API_KEY",
    "LLM_SIMPLE_REQUEST_MAX_CHARS",
    "LLM_PRIMARY_REQUEST_RETRIES",
    "LLM_FALLBACK_REQUEST_RETRIES",
    "LLM_REQUEST_TIMEOUT_SECONDS",
    "LLM_MODEL_FALLBACK_ENABLED",
    "LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY",
    "LLM_MODEL_RELEASES_ENABLED",
    "LLM_CANARY_API_KEY",
    "LLM_ROUTING_ALERT_MIN_REQUESTS",
    "LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE",
    "LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE",
}


def test_compose_injects_model_routing_settings_into_llm_callers():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    for service_name in ("api", "celery_worker"):
        service_start = compose.index(f"  {service_name}:\n")
        next_service = re.search(r"\n  [A-Za-z_][A-Za-z0-9_-]*:\n", compose[service_start + 1 :])
        service_end = len(compose) if next_service is None else service_start + 1 + next_service.start()
        service_block = compose[service_start:service_end]
        for key in ROUTING_KEYS:
            assert f"      {key}:" in service_block
        assert "      LLM_MODEL: ${LLM_MODEL" in service_block
        assert "      LLM_API_BASE_URL: ${LLM_API_BASE_URL" in service_block
