"""
Tests for the P1 "Estudio activo" gap-fill work on top of the existing SM-2
flashcard review flow:

- open_notebook/study/models.py::score_flashcard_review persisting
  `last_rating` (needed for honest accuracy metrics).
- StudySet.review_stats() excluding suspended cards from due_count/next_due.
- StudySet.set_item_suspended() (suspend/resume a card without deleting it).
- api/study_service.py::StudyService.set_item_suspended /
  StudyService.get_metrics (due today, accuracy %, weak sources).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.study_service import StudyService
from open_notebook.exceptions import InvalidInputError
from open_notebook.study.models import StudySet, score_flashcard_review


def _study_set(items=None, kind="flashcards") -> StudySet:
    return StudySet(
        id="study_set:1",
        notebook="notebook:1",
        kind=kind,
        name="Test Set",
        content="content",
        items=items if items is not None else [{"front": "Q1", "back": "A1"}],
    )


class TestScoreFlashcardReviewLastRating:
    def test_records_last_rating_alongside_srs_state(self):
        updated = score_flashcard_review({"front": "Q", "back": "A"}, "good")
        assert updated["last_rating"] == "good"
        assert "ease" in updated and "interval" in updated and "due" in updated

    def test_last_rating_overwritten_on_subsequent_review(self):
        first = score_flashcard_review({"front": "Q", "back": "A"}, "again")
        assert first["last_rating"] == "again"
        second = score_flashcard_review(first, "easy")
        assert second["last_rating"] == "easy"


class TestReviewStatsExcludesSuspended:
    def test_suspended_card_not_counted_as_due(self):
        study_set = _study_set(
            items=[
                {"front": "Q1", "back": "A1"},  # new card, due by default
                {"front": "Q2", "back": "A2", "suspended": True},  # also "due" but suspended
            ]
        )
        stats = study_set.review_stats()
        assert stats["due_count"] == 1

    def test_quiz_kind_always_reports_zero_due(self):
        study_set = _study_set(kind="quiz", items=[{"question": "Q", "options": ["a", "b"], "correct_index": 0}])
        assert study_set.review_stats() == {"due_count": 0, "next_due": None}


class TestSetItemSuspended:
    @pytest.mark.asyncio
    async def test_suspend_then_resume_round_trip(self):
        study_set = _study_set()
        with patch.object(StudySet, "save", new=AsyncMock()):
            suspended_item = await study_set.set_item_suspended(0, True)
            assert suspended_item["suspended"] is True
            assert study_set.review_stats()["due_count"] == 0

            resumed_item = await study_set.set_item_suspended(0, False)
            assert resumed_item["suspended"] is False
            assert study_set.review_stats()["due_count"] == 1

    @pytest.mark.asyncio
    async def test_rejects_quiz_study_sets(self):
        study_set = _study_set(
            kind="quiz", items=[{"question": "Q", "options": ["a", "b"], "correct_index": 0}]
        )
        with pytest.raises(InvalidInputError):
            await study_set.set_item_suspended(0, True)

    @pytest.mark.asyncio
    async def test_rejects_invalid_item_index(self):
        study_set = _study_set()
        with pytest.raises(InvalidInputError):
            await study_set.set_item_suspended(5, True)

    @pytest.mark.asyncio
    async def test_preserves_existing_srs_state(self):
        """Suspending a card must not reset its ease/interval/reps - only
        add the suspended flag."""
        study_set = _study_set(
            items=[{"front": "Q1", "back": "A1", "ease": 2.8, "interval": 6, "reps": 2}]
        )
        with patch.object(StudySet, "save", new=AsyncMock()):
            updated = await study_set.set_item_suspended(0, True)
        assert updated["ease"] == 2.8
        assert updated["interval"] == 6
        assert updated["reps"] == 2
        assert updated["suspended"] is True


class TestStudyServiceSetItemSuspended:
    @pytest.mark.asyncio
    async def test_toggles_via_service_layer(self):
        study_set = _study_set()
        with (
            patch.object(StudyService, "get_study_set", new=AsyncMock(return_value=study_set)),
            patch.object(StudySet, "save", new=AsyncMock()),
        ):
            result = await StudyService.set_item_suspended("study_set:1", 0, True)
        assert result["item"]["suspended"] is True
        assert result["due_count"] == 0

    @pytest.mark.asyncio
    async def test_invalid_index_raises_400(self):
        study_set = _study_set()
        with patch.object(StudyService, "get_study_set", new=AsyncMock(return_value=study_set)):
            with pytest.raises(HTTPException) as exc_info:
                await StudyService.set_item_suspended("study_set:1", 99, True)
        assert exc_info.value.status_code == 400


class TestStudyServiceGetMetrics:
    @pytest.mark.asyncio
    async def test_due_today_and_accuracy_across_notebooks(self):
        reviewed_good = {"front": "Q1", "back": "A1", "last_rating": "good", "source_title": "Chapter 1"}
        reviewed_bad = {"front": "Q2", "back": "A2", "last_rating": "again", "source_title": "Chapter 1"}
        reviewed_bad_2 = {"front": "Q3", "back": "A3", "last_rating": "hard", "source_title": "Chapter 2"}
        unreviewed = {"front": "Q4", "back": "A4"}  # no last_rating -> new/due, not "reviewed"

        set_a = _study_set(items=[reviewed_good, reviewed_bad])
        set_a.notebook = "notebook:1"
        set_b = _study_set(items=[reviewed_bad_2, unreviewed])
        set_b.notebook = "notebook:2"
        quiz_set = _study_set(
            kind="quiz", items=[{"question": "Q", "options": ["a", "b"], "correct_index": 0}]
        )

        with patch.object(StudySet, "get_all", new=AsyncMock(return_value=[set_a, set_b, quiz_set])):
            metrics = await StudyService.get_metrics(notebook_id=None)

        # unreviewed item has no `due` -> counts as due; reviewed items also
        # have no explicit `due` set in this fixture, so due_today counts
        # every flashcard across both sets (quiz excluded).
        assert metrics["due_today"] == 4
        assert metrics["total_cards"] == 4
        assert metrics["reviewed_cards"] == 3
        # 1 of 3 reviewed cards rated good/easy -> 33%
        assert metrics["accuracy_pct"] == round(100 * 1 / 3)

        weak_sources = {w["source_title"]: w for w in metrics["weak_sources"]}
        assert "Chapter 1" in weak_sources
        assert weak_sources["Chapter 1"]["reviewed_count"] == 2
        assert weak_sources["Chapter 1"]["accuracy_pct"] == 50
        # "Chapter 2" only has 1 reviewed card - below the min-2 threshold,
        # so it's excluded rather than skewing the ranking off one review.
        assert "Chapter 2" not in weak_sources

    @pytest.mark.asyncio
    async def test_no_reviews_yet_reports_none_not_zero(self):
        study_set = _study_set(items=[{"front": "Q1", "back": "A1"}])
        with patch.object(StudySet, "get_all", new=AsyncMock(return_value=[study_set])):
            metrics = await StudyService.get_metrics(notebook_id=None)
        assert metrics["accuracy_pct"] is None
        assert metrics["reviewed_cards"] == 0
        assert metrics["weak_sources"] == []

    @pytest.mark.asyncio
    async def test_scoped_to_one_notebook_uses_list_study_sets(self):
        study_set = _study_set(items=[{"front": "Q1", "back": "A1", "last_rating": "easy"}])
        with patch.object(
            StudyService, "list_study_sets", new=AsyncMock(return_value=[study_set])
        ) as mock_list:
            metrics = await StudyService.get_metrics(notebook_id="notebook:1")
        mock_list.assert_awaited_once_with("notebook:1")
        assert metrics["accuracy_pct"] == 100
