"""Tests for P1 "Fuentes e ingestion" lifecycle work:

- content hashing + duplicate detection (open_notebook/utils/content_hash.py,
  Source.find_duplicate, the pre-flight check in create_source())
- retry/replace not duplicating chunks (commands/embedding_commands.py
  embed_source_command's delete-then-insert step)
- archive/unarchive, delete-preview, replace, and upload-limits endpoints
  (api/routers/sources.py)
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.notebook import Source
from open_notebook.utils.content_hash import compute_content_hash


def make_source(**overrides):
    defaults = dict(id="source:test123", title="Test Source", archived=False)
    defaults.update(overrides)
    return Source(**defaults)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


class TestComputeContentHash:
    def test_string_and_bytes_agree(self):
        assert compute_content_hash("hello") == compute_content_hash(b"hello")

    def test_matches_hashlib_sha256(self):
        assert compute_content_hash("hello world") == hashlib.sha256(
            b"hello world"
        ).hexdigest()

    def test_different_content_different_hash(self):
        assert compute_content_hash("a") != compute_content_hash("b")


class TestSourceFindDuplicate:
    @pytest.mark.asyncio
    async def test_no_hash_never_queries(self):
        """A hash-less source is never flagged as a duplicate of anything -
        content_hash=None/''` must short-circuit before any query runs."""
        with patch(
            "open_notebook.domain.notebook.repo_query", new_callable=AsyncMock
        ) as mock_query:
            result = await Source.find_duplicate(None, ["notebook:1"])
            assert result is None
            mock_query.assert_not_called()

            result = await Source.find_duplicate("", ["notebook:1"])
            assert result is None
            mock_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notebooks_never_queries(self):
        with patch(
            "open_notebook.domain.notebook.repo_query", new_callable=AsyncMock
        ) as mock_query:
            result = await Source.find_duplicate("abc123", [])
            assert result is None
            mock_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_match_found_excludes_self(self):
        with patch(
            "open_notebook.domain.notebook.repo_query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = [
                {"id": "source:other", "title": "Existing", "archived": False}
            ]
            result = await Source.find_duplicate(
                "abc123", ["notebook:1"], exclude_id="source:self"
            )

            assert result is not None
            assert str(result.id) == "source:other"

            query_text, params = mock_query.await_args.args
            assert "content_hash = $hash" in query_text
            assert "id != $exclude_id" in query_text
            assert params["hash"] == "abc123"
            assert str(params["exclude_id"]) == "source:self"

    @pytest.mark.asyncio
    async def test_db_error_returns_none_not_raise(self):
        """Duplicate detection is a UX nicety - a DB hiccup must never break
        ingestion."""
        with patch(
            "open_notebook.domain.notebook.repo_query",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await Source.find_duplicate("abc123", ["notebook:1"])
            assert result is None


class TestSourceGetDeletePreview:
    @pytest.mark.asyncio
    async def test_returns_all_counts(self):
        source = make_source()

        async def fake_repo_query(query, params=None):
            if "source_embedding" in query:
                return [{"count": 3}]
            if "source_insight" in query:
                return [{"count": 2}]
            if "refers_to" in query:
                return [{"count": 1}]
            if "FROM reference" in query:
                return [{"count": 4}]
            return []

        with patch(
            "open_notebook.domain.notebook.repo_query",
            new_callable=AsyncMock,
            side_effect=fake_repo_query,
        ):
            preview = await source.get_delete_preview()

        assert preview == {
            "chunk_count": 3,
            "insight_count": 2,
            "chat_session_count": 1,
            "notebook_count": 4,
        }

    @pytest.mark.asyncio
    async def test_empty_results_default_to_zero(self):
        source = make_source()
        with patch(
            "open_notebook.domain.notebook.repo_query",
            new_callable=AsyncMock,
            return_value=[],
        ):
            preview = await source.get_delete_preview()

        assert preview == {
            "chunk_count": 0,
            "insight_count": 0,
            "chat_session_count": 0,
            "notebook_count": 0,
        }


class TestEmbedSourceRetryDoesNotDuplicateChunks:
    """Retrying a failed/partial source must not double its chunk count.

    embed_source_command already DELETEs existing source_embedding rows for
    the source before re-chunking/re-embedding (see commands/
    embedding_commands.py embed_source_command, step 2) - this test locks
    that idempotency in and would fail if that delete step were ever
    removed/reordered after the insert.
    """

    @pytest.mark.asyncio
    async def test_retry_with_existing_partial_chunks_does_not_double(self):
        from commands.embedding_commands import EmbedSourceInput, embed_source_command

        source = MagicMock()
        source.id = "source:1"
        source.full_text = "Some long document content to chunk and embed."
        source.asset = None

        # Simulate a prior partial run that already left 2 chunks behind.
        existing_chunks = [
            {"id": "source_embedding:1", "source": "source:1"},
            {"id": "source_embedding:2", "source": "source:1"},
        ]
        delete_calls = []

        async def fake_repo_query(query, params=None):
            if query.strip().startswith("DELETE source_embedding"):
                delete_calls.append(params)
                deleted = list(existing_chunks)
                existing_chunks.clear()
                return deleted
            return []

        new_chunks = ["chunk one", "chunk two", "chunk three"]
        new_embeddings = [[0.1], [0.2], [0.3]]
        inserted_records = []

        async def fake_repo_insert(table, records):
            inserted_records.extend(records)
            return records

        with (
            patch(
                "commands.embedding_commands.Source.get",
                new_callable=AsyncMock,
                return_value=source,
            ),
            patch(
                "commands.embedding_commands.repo_query",
                new_callable=AsyncMock,
                side_effect=fake_repo_query,
            ),
            patch(
                "commands.embedding_commands.repo_insert",
                new_callable=AsyncMock,
                side_effect=fake_repo_insert,
            ),
            patch(
                "commands.embedding_commands.detect_content_type",
                return_value=MagicMock(value="markdown"),
            ),
            patch(
                "commands.embedding_commands.chunk_text",
                return_value=new_chunks,
            ),
            patch(
                "commands.embedding_commands.generate_embeddings",
                new_callable=AsyncMock,
                return_value=new_embeddings,
            ),
        ):
            result = await embed_source_command(EmbedSourceInput(source_id="source:1"))

        assert result.success is True
        assert result.chunks_created == len(new_chunks)
        # The old 2 chunks must have been deleted - not left alongside the
        # new 3 - and exactly the new 3 inserted (no accumulation to 5).
        assert len(delete_calls) == 1
        assert len(inserted_records) == len(new_chunks)
        assert existing_chunks == []


def make_mock_source(**overrides):
    """A MagicMock source for endpoint tests that need to stub out methods
    like .save()/.get_delete_preview() - real pydantic Source instances
    reject assigning non-field attributes."""
    source = MagicMock()
    source.id = "source:test123"
    source.title = "Test Source"
    source.archived = False
    source.duplicate_of = None
    source.asset = None
    source.full_text = None
    source.command = None
    source.created = "2024-01-01 00:00:00"
    source.updated = "2024-01-01 00:00:00"
    source.topics = []
    source.save = AsyncMock()
    source.get_embedded_chunks = AsyncMock(return_value=0)
    for key, value in overrides.items():
        setattr(source, key, value)
    return source


class TestArchiveUnarchiveEndpoints:
    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_archive_sets_flag_and_persists(self, mock_get, client):
        source = make_mock_source(archived=False)
        mock_get.return_value = source

        response = client.post("/api/sources/source:test123/archive")

        assert response.status_code == 200
        assert response.json()["archived"] is True
        source.save.assert_awaited()

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_unarchive_clears_flag(self, mock_get, client):
        source = make_mock_source(archived=True)
        mock_get.return_value = source

        response = client.post("/api/sources/source:test123/unarchive")

        assert response.status_code == 200
        assert response.json()["archived"] is False

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_archive_missing_source_404(self, mock_get, client):
        mock_get.return_value = None
        response = client.post("/api/sources/source:gone/archive")
        assert response.status_code == 404


class TestUploadLimitsEndpoint:
    def test_returns_configured_limit(self, client, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_MAX_UPLOAD_SIZE_MB", "42")
        response = client.get("/api/sources/limits")

        assert response.status_code == 200
        body = response.json()
        assert body["max_upload_size_mb"] == 42
        assert body["max_upload_size_bytes"] == 42 * 1024 * 1024

    def test_limits_route_not_shadowed_by_source_id_route(self, client):
        """'/sources/limits' must resolve to the limits endpoint, not be
        swallowed by GET /sources/{source_id} with source_id='limits'."""
        with patch(
            "api.routers.sources.Source.get", new_callable=AsyncMock
        ) as mock_get:
            response = client.get("/api/sources/limits")
            assert response.status_code == 200
            mock_get.assert_not_called()


class TestDeletePreviewEndpoint:
    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_returns_counts(self, mock_get, client):
        source = make_mock_source()
        source.get_delete_preview = AsyncMock(
            return_value={
                "chunk_count": 5,
                "insight_count": 2,
                "chat_session_count": 1,
                "notebook_count": 3,
            }
        )
        mock_get.return_value = source

        response = client.get("/api/sources/source:test123/delete-preview")

        assert response.status_code == 200
        body = response.json()
        assert body["chunk_count"] == 5
        assert body["insight_count"] == 2
        assert body["chat_session_count"] == 1
        assert body["notebook_count"] == 3

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_missing_source_404(self, mock_get, client):
        mock_get.return_value = None
        response = client.get("/api/sources/source:gone/delete-preview")
        assert response.status_code == 404


class TestDuplicateDetectionOnCreate:
    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.find_duplicate", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_text_duplicate_returns_409_with_details(
        self, mock_nb_get, mock_find_dup, client
    ):
        mock_nb_get.return_value = MagicMock()
        existing = make_source(id="source:existing", title="Original")
        mock_find_dup.return_value = existing

        response = client.post(
            "/api/sources",
            data={
                "type": "text",
                "content": "duplicate content",
                "notebooks": '["notebook:1"]',
                "async_processing": "true",
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["duplicate"] is True
        assert detail["existing_source_id"] == "source:existing"
        assert detail["existing_source_title"] == "Original"

    @pytest.mark.asyncio
    @patch("api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.find_duplicate", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_force_duplicate_skips_check_and_creates(
        self, mock_nb_get, mock_find_dup, mock_add_nb, mock_submit, client
    ):
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with patch.object(Source, "save", autospec=True, side_effect=capture_save):
            response = client.post(
                "/api/sources",
                data={
                    "type": "text",
                    "content": "duplicate content",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "true",
                    "force_duplicate": "true",
                },
            )

        assert response.status_code == 200
        mock_find_dup.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.add_to_notebook", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.find_duplicate", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    async def test_link_type_skips_pre_check(
        self, mock_nb_get, mock_find_dup, mock_add_nb, mock_submit, client
    ):
        """Link sources aren't checked pre-creation - content only exists
        after async extraction. Covered post-hoc by save_source() instead."""
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"

        async def capture_save(self_source):
            self_source.id = "source:fake"
            self_source.command = None

        with (
            patch.object(Source, "save", autospec=True, side_effect=capture_save),
            patch(
                "api.routers.sources.validate_url",
                new_callable=AsyncMock,
            ),
        ):
            response = client.post(
                "/api/sources",
                data={
                    "type": "link",
                    "url": "https://example.com/article",
                    "notebooks": '["notebook:1"]',
                    "async_processing": "true",
                },
            )

        assert response.status_code == 200
        mock_find_dup.assert_not_called()


class TestReplaceSourceContent:
    @pytest.mark.asyncio
    @patch("api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_query", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_replace_clears_old_insights_and_resubmits(
        self, mock_get, mock_repo_query, mock_submit, client
    ):
        source = make_mock_source()
        mock_get.return_value = source
        mock_repo_query.return_value = ["notebook:1"]
        mock_submit.return_value = "command:999"

        response = client.post(
            "/api/sources/source:test123/replace",
            data={"type": "text", "content": "brand new content"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"

        # One of the repo_query calls must be the insight-clearing DELETE.
        delete_calls = [
            call
            for call in mock_repo_query.await_args_list
            if call.args and "DELETE source_insight" in call.args[0]
        ]
        assert len(delete_calls) == 1
        assert str(source.command).startswith("command:")

    @pytest.mark.asyncio
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    async def test_replace_missing_source_404(self, mock_get, client):
        mock_get.return_value = None
        response = client.post(
            "/api/sources/source:gone/replace",
            data={"type": "text", "content": "x"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_replace_invalid_type_400(self, client):
        response = client.post(
            "/api/sources/source:test123/replace",
            data={"type": "bogus"},
        )
        assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
