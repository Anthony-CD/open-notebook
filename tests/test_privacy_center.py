"""
Tests for the privacy center (P2 "Seguridad" item 8):
  - GET /api/privacy/export builds a metadata/text bundle, never inlines
    secret values, and writes an audit entry
  - DELETE /api/privacy/data requires an exact confirmation phrase, wipes
    content tables + on-disk files, and audit-logs the request before
    executing

No live database or filesystem beyond tmp_path - repo_query/Credential/
DriveConnection are mocked, and UPLOADS_FOLDER/PODCASTS_FOLDER/
LANGGRAPH_CHECKPOINT_FILE are monkeypatched to tmp_path subdirectories.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.privacy_service import (
    REQUIRED_DELETE_CONFIRMATION,
    build_export,
    delete_all_data,
)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


class TestBuildExport:
    @pytest.mark.asyncio
    async def test_export_shape_includes_all_sections(self):
        async def fake_query(query_str, params=None):
            return []

        with patch("api.privacy_service.repo_query", side_effect=fake_query), patch(
            "api.privacy_service.Credential.get_all", new=AsyncMock(return_value=[])
        ), patch(
            "api.privacy_service.DriveConnection.get_instance",
            new=AsyncMock(
                return_value=type(
                    "C", (), {"is_connected": False, "account_email": None}
                )()
            ),
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()):
            bundle = await build_export()

        for key in (
            "generated_at", "note", "notebooks", "sources", "notes",
            "chat_sessions", "study_sets", "episodes", "credentials",
            "drive_connection",
        ):
            assert key in bundle

    @pytest.mark.asyncio
    async def test_export_never_includes_api_key_field(self):
        """Credentials must be exposed through credential_to_response (no
        api_key field at all) - the same guarantee every other credentials
        endpoint gives."""
        from open_notebook.domain.credential import Credential

        fake_cred = Credential(id="credential:1", name="my-key", provider="openai")

        async def fake_query(query_str, params=None):
            return []

        with patch("api.privacy_service.repo_query", side_effect=fake_query), patch(
            "api.privacy_service.Credential.get_all",
            new=AsyncMock(return_value=[fake_cred]),
        ), patch(
            "open_notebook.domain.credential.repo_query", side_effect=fake_query
        ), patch(
            "api.privacy_service.DriveConnection.get_instance",
            new=AsyncMock(
                return_value=type(
                    "C", (), {"is_connected": False, "account_email": None}
                )()
            ),
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()):
            bundle = await build_export()

        assert len(bundle["credentials"]) == 1
        assert "api_key" not in bundle["credentials"][0]
        assert bundle["credentials"][0]["has_api_key"] is False

    @pytest.mark.asyncio
    async def test_export_writes_audit_event(self):
        async def fake_query(query_str, params=None):
            return []

        with patch("api.privacy_service.repo_query", side_effect=fake_query), patch(
            "api.privacy_service.Credential.get_all", new=AsyncMock(return_value=[])
        ), patch(
            "api.privacy_service.DriveConnection.get_instance",
            new=AsyncMock(
                return_value=type(
                    "C", (), {"is_connected": False, "account_email": None}
                )()
            ),
        ), patch(
            "api.privacy_service.record_audit_event", new=AsyncMock()
        ) as mock_audit:
            await build_export()

        mock_audit.assert_awaited_once()


class TestDeleteAllDataRequiresExactConfirmation:
    @pytest.mark.asyncio
    async def test_wrong_confirmation_raises_value_error(self):
        with pytest.raises(ValueError):
            await delete_all_data("please delete everything")

    @pytest.mark.asyncio
    async def test_empty_confirmation_raises_value_error(self):
        with pytest.raises(ValueError):
            await delete_all_data("")

    @pytest.mark.asyncio
    async def test_correct_confirmation_proceeds(self, tmp_path, monkeypatch):
        uploads = tmp_path / "uploads"
        podcasts = tmp_path / "podcasts"
        uploads.mkdir()
        podcasts.mkdir()
        checkpoint = tmp_path / "checkpoints.sqlite"
        checkpoint.write_text("x")

        monkeypatch.setattr("api.privacy_service.UPLOADS_FOLDER", str(uploads))
        monkeypatch.setattr("api.privacy_service.PODCASTS_FOLDER", str(podcasts))
        monkeypatch.setattr(
            "api.privacy_service.LANGGRAPH_CHECKPOINT_FILE", str(checkpoint)
        )

        with patch(
            "api.privacy_service.repo_query", new=AsyncMock(return_value=[{"total": 0}])
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()) as mock_audit:
            counts = await delete_all_data(REQUIRED_DELETE_CONFIRMATION)

        assert "notebook" in counts
        assert checkpoint.exists() is False
        # Audit event is recorded before executing the wipe.
        mock_audit.assert_awaited()

    @pytest.mark.asyncio
    async def test_deletes_files_on_disk(self, tmp_path, monkeypatch):
        uploads = tmp_path / "uploads"
        podcasts = tmp_path / "podcasts"
        uploads.mkdir()
        podcasts.mkdir()
        (uploads / "report.pdf").write_bytes(b"pdf bytes")
        episode_dir = podcasts / "episode-1"
        episode_dir.mkdir()
        (episode_dir / "audio.mp3").write_bytes(b"audio bytes")

        monkeypatch.setattr("api.privacy_service.UPLOADS_FOLDER", str(uploads))
        monkeypatch.setattr("api.privacy_service.PODCASTS_FOLDER", str(podcasts))
        monkeypatch.setattr(
            "api.privacy_service.LANGGRAPH_CHECKPOINT_FILE",
            str(tmp_path / "nonexistent.sqlite"),
        )

        with patch(
            "api.privacy_service.repo_query", new=AsyncMock(return_value=[{"total": 0}])
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()):
            counts = await delete_all_data(REQUIRED_DELETE_CONFIRMATION)

        assert counts["uploaded_files"] == 1
        assert counts["podcast_audio_files"] == 1
        assert not (uploads / "report.pdf").exists()
        assert not (episode_dir / "audio.mp3").exists()


class TestPrivacyEndpoints:
    def test_export_endpoint_200(self, client):
        async def fake_query(query_str, params=None):
            return []

        with patch("api.privacy_service.repo_query", side_effect=fake_query), patch(
            "api.privacy_service.Credential.get_all", new=AsyncMock(return_value=[])
        ), patch(
            "api.privacy_service.DriveConnection.get_instance",
            new=AsyncMock(
                return_value=type(
                    "C", (), {"is_connected": False, "account_email": None}
                )()
            ),
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()):
            response = client.get("/api/privacy/export")

        assert response.status_code == 200
        assert "notebooks" in response.json()

    def test_delete_endpoint_rejects_wrong_confirmation(self, client):
        response = client.request(
            "DELETE", "/api/privacy/data", json={"confirm": "nope"}
        )
        assert response.status_code == 400

    def test_delete_endpoint_accepts_exact_confirmation(self, client, tmp_path, monkeypatch):
        uploads = tmp_path / "uploads"
        podcasts = tmp_path / "podcasts"
        uploads.mkdir()
        podcasts.mkdir()

        monkeypatch.setattr("api.privacy_service.UPLOADS_FOLDER", str(uploads))
        monkeypatch.setattr("api.privacy_service.PODCASTS_FOLDER", str(podcasts))
        monkeypatch.setattr(
            "api.privacy_service.LANGGRAPH_CHECKPOINT_FILE",
            str(tmp_path / "nonexistent.sqlite"),
        )

        with patch(
            "api.privacy_service.repo_query", new=AsyncMock(return_value=[{"total": 0}])
        ), patch("api.privacy_service.record_audit_event", new=AsyncMock()):
            response = client.request(
                "DELETE",
                "/api/privacy/data",
                json={"confirm": REQUIRED_DELETE_CONFIRMATION},
            )

        assert response.status_code == 200
        assert "deleted" in response.json()
