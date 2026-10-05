"""Tests for the P1 "Transformaciones" work: prompt preview, cost estimate,
in-flight duplicate guard, real post-execution cost, and result versioning.

No live DB (Docker off): every DB-touching call is mocked. No paid model
calls: the LLM graph invocation is mocked.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.transformation import Transformation


def _client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _transformation(model_id=None, title="Summary") -> Transformation:
    return Transformation(
        id="transformation:123",
        name="summary",
        title=title,
        description="Summarize the source",
        prompt="Summarize this",
        apply_default=False,
        model_id=model_id,
        created=datetime(2026, 1, 1, 12, 0, 0),
        updated=datetime(2026, 1, 1, 12, 0, 0),
    )


def _no_default_prompts():
    return patch(
        "api.routers.transformations.DefaultPrompts.get_instance",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(transformation_instructions=None),
    )


# ---------------------------------------------------------------------------
# 1. Preview before saving
# ---------------------------------------------------------------------------


def test_preview_renders_instructions_and_default_sample():
    client = _client()
    with _no_default_prompts():
        response = client.post(
            "/api/transformations/preview",
            json={"prompt": "Extract the key topics."},
        )

    assert response.status_code == 200
    body = response.json()
    assert "Extract the key topics." in body["system_prompt"]
    # Source content is never interpolated into the system prompt.
    assert body["sample_input"] not in body["system_prompt"]
    assert body["sample_input"]


def test_preview_uses_provided_sample_and_prepends_default_instructions():
    client = _client()
    with patch(
        "api.routers.transformations.DefaultPrompts.get_instance",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(transformation_instructions="Be concise."),
    ):
        response = client.post(
            "/api/transformations/preview",
            json={"prompt": "Extract the key topics.", "sample_text": "Hello world"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "Be concise." in body["system_prompt"]
    assert "Extract the key topics." in body["system_prompt"]
    assert body["sample_input"] == "Hello world"


# ---------------------------------------------------------------------------
# 2. Cost estimate before execution
# ---------------------------------------------------------------------------


def test_estimate_cost_known_price():
    client = _client()
    transformation = _transformation(model_id="model:haiku")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(provider="anthropic", name="claude-haiku-4-5"),
        ),
        _no_default_prompts(),
    ):
        response = client.post(
            "/api/transformations/estimate-cost",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some source content " * 50,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["cost_status"] == "known"
    assert body["estimated_cost_usd"] is not None
    assert body["estimated_cost_usd"] > 0
    assert body["estimated_input_tokens"] > 0
    assert body["is_estimate"] is True


def test_estimate_cost_unknown_price_never_invents_zero():
    client = _client()
    transformation = _transformation(model_id="model:mystery")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(provider="some_unknown_provider", name="mystery-model"),
        ),
        _no_default_prompts(),
    ):
        response = client.post(
            "/api/transformations/estimate-cost",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some text",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["cost_status"] == "unknown"
    assert body["estimated_cost_usd"] is None


def test_estimate_cost_no_model_configured_is_unknown_not_zero():
    client = _client()
    transformation = _transformation(model_id=None)

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.DefaultModels.get_instance",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                default_transformation_model=None, default_chat_model=None
            ),
        ),
        _no_default_prompts(),
    ):
        response = client.post(
            "/api/transformations/estimate-cost",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some text",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] is None
    assert body["cost_status"] == "unknown"
    assert body["estimated_cost_usd"] is None


def test_estimate_cost_free_model():
    client = _client()
    transformation = _transformation(model_id="model:local")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(provider="ollama", name="llama3"),
        ),
        _no_default_prompts(),
    ):
        response = client.post(
            "/api/transformations/estimate-cost",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some text",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["cost_status"] == "free"
    assert body["estimated_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 3. Duplicate-run guard on /transformations/execute
# ---------------------------------------------------------------------------


def test_execute_rejects_duplicate_in_flight_request():
    """A request for a (transformation, model, input text) already marked
    in-flight is rejected with 409 - this is the smallest honest duplicate
    guard described in the task brief (no queue to hook real cancellation
    into for this synchronous preview/test endpoint - see the module-level
    docstring on _IN_FLIGHT_EXECUTIONS in api/routers/transformations.py).

    Driven directly against the module's guard state (rather than racing
    two real concurrent requests through TestClient's own event loop/thread
    handling, which is fragile to test reliably) - this exercises exactly
    the code path execute_transformation() checks.
    """
    from api.routers.transformations import _IN_FLIGHT_EXECUTIONS, _execution_key

    client = _client()
    transformation = _transformation(model_id="model:stored")
    payload = {
        "transformation_id": transformation.id,
        "input_text": "Same input",
    }
    key = _execution_key(payload["transformation_id"], None, payload["input_text"])

    _IN_FLIGHT_EXECUTIONS.add(key)
    try:
        response = client.post("/api/transformations/execute", json=payload)
    finally:
        _IN_FLIGHT_EXECUTIONS.discard(key)

    assert response.status_code == 409


def test_execute_allows_sequential_requests_after_completion():
    """The in-flight guard must release its key once a run finishes, so the
    same transformation+input can be re-run afterwards (no permanent lock).
    """
    client = _client()
    transformation = _transformation(model_id="model:stored")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=object(),
        ),
        patch(
            "api.routers.transformations.transformation_graph.ainvoke",
            new_callable=AsyncMock,
            return_value={"output": "done"},
        ),
    ):
        payload = {
            "transformation_id": transformation.id,
            "input_text": "Same input twice",
        }
        first = client.post("/api/transformations/execute", json=payload)
        second = client.post("/api/transformations/execute", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200


# ---------------------------------------------------------------------------
# 4. Real cost after execution
# ---------------------------------------------------------------------------


def test_execute_attaches_real_cost_from_usage_event():
    client = _client()
    transformation = _transformation(model_id="model:stored")

    fake_event_row = {
        "provider": "anthropic",
        "model_name": "claude-haiku-4-5",
        "task_type": "transformation",
        "input_tokens": 120,
        "output_tokens": 45,
        "cached_input_tokens": None,
        "estimated_cost_usd": 0.0003,
        "cost_status": "known",
        "failed": False,
        "created": datetime(2026, 1, 1, 12, 0, 1),
    }

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=object(),
        ),
        patch(
            "api.routers.transformations.transformation_graph.ainvoke",
            new_callable=AsyncMock,
            return_value={"output": "done"},
        ),
        patch(
            "api.routers.transformations.UsageEvent.fetch_rows",
            new_callable=AsyncMock,
            return_value=[fake_event_row],
        ),
    ):
        response = client.post(
            "/api/transformations/execute",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some input",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["input_tokens"] == 120
    assert body["output_tokens"] == 45
    assert body["cost_status"] == "known"
    assert body["actual_cost_usd"] == 0.0003


def test_execute_without_usage_event_leaves_cost_fields_none():
    client = _client()
    transformation = _transformation(model_id="model:stored")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Model.get",
            new_callable=AsyncMock,
            return_value=object(),
        ),
        patch(
            "api.routers.transformations.transformation_graph.ainvoke",
            new_callable=AsyncMock,
            return_value={"output": "done"},
        ),
        patch(
            "api.routers.transformations.UsageEvent.fetch_rows",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        response = client.post(
            "/api/transformations/execute",
            json={
                "transformation_id": transformation.id,
                "input_text": "Some other input",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["actual_cost_usd"] is None
    assert body["cost_status"] is None


# ---------------------------------------------------------------------------
# 5. Result versioning (domain method + endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_result_versions_computes_position_when_version_unset():
    transformation = _transformation(title="Key Topics")
    rows = [
        {"id": "source_insight:1", "content": "first run", "version": None, "created": datetime(2026, 1, 1)},
        {"id": "source_insight:2", "content": "second run", "version": None, "created": datetime(2026, 1, 2)},
    ]

    with patch(
        "open_notebook.domain.transformation.repo_query",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        versions = await transformation.get_result_versions("source:abc")

    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["is_latest"] is False
    assert versions[1]["is_latest"] is True
    assert versions[1]["content"] == "second run"


@pytest.mark.asyncio
async def test_get_result_versions_trusts_explicit_version_over_position():
    transformation = _transformation(title="Key Topics")
    rows = [
        {"id": "source_insight:1", "content": "first run", "version": 1, "created": datetime(2026, 1, 1)},
        {"id": "source_insight:2", "content": "second run", "version": 5, "created": datetime(2026, 1, 2)},
    ]

    with patch(
        "open_notebook.domain.transformation.repo_query",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        versions = await transformation.get_result_versions("source:abc")

    assert [v["version"] for v in versions] == [1, 5]


@pytest.mark.asyncio
async def test_get_result_versions_empty_when_never_run():
    transformation = _transformation(title="Key Topics")

    with patch(
        "open_notebook.domain.transformation.repo_query",
        new_callable=AsyncMock,
        return_value=[],
    ):
        versions = await transformation.get_result_versions("source:abc")

    assert versions == []


def test_result_history_endpoint_returns_versions():
    client = _client()
    transformation = _transformation(title="Key Topics")
    source = SimpleNamespace(id="source:abc")

    versions = [
        {"version": 1, "content": "first", "created": "2026-01-01T00:00:00", "is_latest": False},
        {"version": 2, "content": "second", "created": "2026-01-02T00:00:00", "is_latest": True},
    ]

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Source.get",
            new_callable=AsyncMock,
            return_value=source,
        ),
        patch.object(
            Transformation,
            "get_result_versions",
            new_callable=AsyncMock,
            return_value=versions,
        ),
    ):
        response = client.get(
            f"/api/transformations/{transformation.id}/results/source:abc"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["transformation_id"] == transformation.id
    assert body["source_id"] == "source:abc"
    assert len(body["versions"]) == 2
    assert body["versions"][-1]["is_latest"] is True


def test_result_history_endpoint_404_when_source_missing():
    client = _client()
    transformation = _transformation(title="Key Topics")

    with (
        patch(
            "api.routers.transformations.Transformation.get",
            new_callable=AsyncMock,
            return_value=transformation,
        ),
        patch(
            "api.routers.transformations.Source.get",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = client.get(
            f"/api/transformations/{transformation.id}/results/source:missing"
        )

    assert response.status_code == 404
