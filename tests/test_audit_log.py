"""
Tests for the audit log (P2 "Seguridad" item 7):
  - api.audit_service.record_audit_event() never raises, even on a DB error
  - api.audit_service.list_audit_events() builds the right filtered/paginated
    query and shapes rows for the API
  - GET /api/activity returns a paginated, filterable view

No live database - repo_query/AuditLog.save are mocked throughout.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.audit_service import (
    ACTION_NOTEBOOK_DELETED,
    list_audit_events,
    record_audit_event,
)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


class TestRecordAuditEventNeverRaises:
    @pytest.mark.asyncio
    async def test_successful_write_calls_save(self):
        with patch(
            "api.audit_service.AuditLog.save", new=AsyncMock(return_value=None)
        ) as mock_save:
            await record_audit_event(ACTION_NOTEBOOK_DELETED, detail="My Notebook")
        mock_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_failure_is_swallowed_not_raised(self):
        with patch(
            "api.audit_service.AuditLog.save",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            # Must not raise - the caller's mutation (e.g. a notebook
            # delete) must succeed even if the audit write fails.
            await record_audit_event(ACTION_NOTEBOOK_DELETED, detail="My Notebook")

    @pytest.mark.asyncio
    async def test_detail_is_truncated_defensively(self):
        huge_detail = "x" * 10_000
        with patch(
            "api.audit_service.AuditLog.save", new=AsyncMock(return_value=None)
        ):
            with patch("api.audit_service.AuditLog") as mock_cls:
                mock_cls.return_value.save = AsyncMock(return_value=None)
                await record_audit_event(ACTION_NOTEBOOK_DELETED, detail=huge_detail)
                _, kwargs = mock_cls.call_args
                assert len(kwargs["detail"]) <= 300

    @pytest.mark.asyncio
    async def test_none_detail_is_allowed(self):
        with patch(
            "api.audit_service.AuditLog.save", new=AsyncMock(return_value=None)
        ) as mock_save:
            await record_audit_event(ACTION_NOTEBOOK_DELETED, detail=None)
        mock_save.assert_awaited_once()


class TestListAuditEventsFilteringAndPagination:
    @pytest.mark.asyncio
    async def test_no_filters_queries_without_where_clause(self):
        with patch(
            "api.audit_service.repo_query", new=AsyncMock(return_value=[])
        ) as mock_query:
            await list_audit_events(limit=10, offset=0)

        first_call_sql = mock_query.call_args_list[0].args[0]
        assert "WHERE" not in first_call_sql
        assert "ORDER BY created DESC" in first_call_sql
        assert "LIMIT $limit START $offset" in first_call_sql

    @pytest.mark.asyncio
    async def test_action_filter_is_applied(self):
        with patch(
            "api.audit_service.repo_query", new=AsyncMock(return_value=[])
        ) as mock_query:
            await list_audit_events(action="notebook_deleted", limit=10, offset=0)

        first_call = mock_query.call_args_list[0]
        assert "action = $action" in first_call.args[0]
        assert first_call.args[1]["action"] == "notebook_deleted"

    @pytest.mark.asyncio
    async def test_returns_items_and_total_from_two_queries(self):
        rows = [
            {"id": "audit_log:1", "action": "notebook_deleted", "detail": "X", "created": "2026-01-01T00:00:00Z"},
        ]
        count_rows = [{"total": 1}]

        async def fake_query(query_str, params=None):
            if "count()" in query_str:
                return count_rows
            return rows

        with patch("api.audit_service.repo_query", side_effect=fake_query):
            items, total = await list_audit_events(limit=10, offset=0)

        assert total == 1
        assert items[0]["action"] == "notebook_deleted"
        assert items[0]["detail"] == "X"

    @pytest.mark.asyncio
    async def test_empty_result_yields_zero_total(self):
        async def fake_query(query_str, params=None):
            if "count()" in query_str:
                return [{"total": 0}]
            return []

        with patch("api.audit_service.repo_query", side_effect=fake_query):
            items, total = await list_audit_events(limit=10, offset=0)

        assert items == []
        assert total == 0


class TestActivityEndpoint:
    def test_get_activity_returns_paginated_shape(self, client):
        rows = [
            {"id": "audit_log:1", "action": "source_deleted", "detail": "report.pdf", "created": "2026-01-01T00:00:00Z"},
        ]

        async def fake_query(query_str, params=None):
            if "count()" in query_str:
                return [{"total": 1}]
            return rows

        with patch("api.audit_service.repo_query", side_effect=fake_query):
            response = client.get("/api/activity")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "source_deleted"
        assert body["items"][0]["detail"] == "report.pdf"

    def test_get_activity_accepts_filters(self, client):
        with patch(
            "api.audit_service.repo_query", new=AsyncMock(return_value=[])
        ) as mock_query:
            response = client.get(
                "/api/activity",
                params={"action": "login_failed", "limit": 5, "offset": 10},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 5
        assert body["offset"] == 10
        assert mock_query.await_count >= 1

    def test_get_activity_handles_query_failure_as_500(self, client):
        with patch(
            "api.audit_service.repo_query",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            response = client.get("/api/activity")
        assert response.status_code == 500
