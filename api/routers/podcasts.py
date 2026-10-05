import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from api.podcast_service import (
    PodcastGenerationEstimateResponse,
    PodcastGenerationRequest,
    PodcastGenerationResponse,
    PodcastService,
)
from open_notebook.ai.models import Model
from open_notebook.exceptions import OpenNotebookError
from open_notebook.podcasts.audio_paths import resolve_contained_audio_path
from open_notebook.podcasts.models import PodcastEpisode
from open_notebook.utils.job_status import normalize_job_status, sanitize_job_error

router = APIRouter()

# Model reference fields stored in the denormalized profile snapshots on an
# episode, mapped to the resolved display fields the frontend renders
# ("provider / name" rows in EpisodeCard). Mirrors the speaker_config ->
# speaker_config_name precedent in api/routers/episode_profiles.py.
_EPISODE_PROFILE_MODEL_FIELDS = {
    "outline_llm": ("outline_model_provider", "outline_model_name"),
    "transcript_llm": ("transcript_model_provider", "transcript_model_name"),
}
_SPEAKER_PROFILE_MODEL_FIELDS = {
    "voice_model": ("voice_model_provider", "voice_model_name"),
}


def _collect_snapshot_model_ids(episodes: List[PodcastEpisode]) -> List[str]:
    """Collect the distinct model record IDs referenced by episode snapshots."""
    ids = set()
    for episode in episodes:
        for field in _EPISODE_PROFILE_MODEL_FIELDS:
            ref = (episode.episode_profile or {}).get(field)
            if ref:
                ids.add(str(ref))
        for field in _SPEAKER_PROFILE_MODEL_FIELDS:
            ref = (episode.speaker_profile or {}).get(field)
            if ref:
                ids.add(str(ref))
    return sorted(ids)


def _with_resolved_model_fields(
    snapshot: dict,
    field_map: dict,
    models_by_id: dict,
) -> dict:
    """Return a copy of a profile snapshot with resolved model display fields.

    Only sets the display fields when the reference resolves; unresolvable
    references (deleted model) and legacy snapshots without references are
    left untouched so the frontend can fall back to the historical
    provider/model strings, then to a placeholder.
    """
    enriched = dict(snapshot or {})
    for ref_field, (provider_field, name_field) in field_map.items():
        ref = enriched.get(ref_field)
        info = models_by_id.get(str(ref)) if ref else None
        if info:
            enriched[provider_field] = info["provider"]
            enriched[name_field] = info["name"]
    return enriched


async def _resolve_snapshot_models(
    episodes: List[PodcastEpisode],
) -> dict:
    """Batch-resolve every model reference in the episodes' snapshots.

    One query for the whole list (see Model.get_display_info_for_ids) - a
    failure degrades to no resolved fields rather than failing the request.
    """
    try:
        return await Model.get_display_info_for_ids(
            _collect_snapshot_model_ids(episodes)
        )
    except Exception as e:
        logger.warning(f"Error batch-resolving snapshot model references: {str(e)}")
        return {}


def _delete_episode_audio(episode: PodcastEpisode, episode_id: str) -> None:
    """Best-effort unlink of an episode's audio file, refusing invalid paths.

    Shared by the delete and retry endpoints. Legacy/escaping audio_file
    values (resolve_contained_audio_path -> None) are logged and skipped.
    """
    if not episode.audio_file:
        return
    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is None:
        logger.warning(
            f"Refusing to delete audio file outside podcasts directory "
            f"for episode {episode_id}: {episode.audio_file}"
        )
    elif audio_path.exists():
        try:
            audio_path.unlink()
            logger.info(f"Deleted audio file: {audio_path}")
        except Exception as e:
            logger.warning(f"Failed to delete audio file {audio_path}: {e}")


class PodcastEpisodeResponse(BaseModel):
    id: str
    name: str
    episode_profile: dict
    speaker_profile: dict
    briefing: str
    audio_file: Optional[str] = None
    audio_url: Optional[str] = None
    audio_size_bytes: Optional[int] = None
    transcript: Optional[dict] = None
    outline: Optional[dict] = None
    created: Optional[str] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    command_id: Optional[str] = None
    favorited: bool = False
    tags: List[str] = []
    content_notebooks: List[str] = []


class PodcastEpisodeOrganizationUpdate(BaseModel):
    """Body for PATCH /podcasts/episodes/{episode_id} (favorite/tag only -
    episode content/profile snapshots are immutable after generation)."""

    favorited: Optional[bool] = None
    tags: Optional[List[str]] = None


_NOTEBOOK_LABEL_PATTERN = re.compile(r"^(?:Notebook|Cuaderno): (.+)$", re.MULTILINE)


def _resolve_audio_url_and_size(episode: PodcastEpisode) -> tuple:
    """Resolve the protected audio URL and the file's size on disk.

    Shared by list/get so both expose audio_size_bytes - a cheap stat() call
    (not a read) that lets the frontend show file size BEFORE fetching the
    audio (see EpisodeCard's lazy load-to-play button, P1 "no silent large
    downloads").
    """
    audio_url = None
    audio_size_bytes = None
    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is not None and audio_path.exists():
        audio_url = f"/api/podcasts/episodes/{episode.id}/audio"
        try:
            audio_size_bytes = audio_path.stat().st_size
        except OSError as e:
            logger.warning(f"Could not stat audio file for episode {episode.id}: {e}")
    return audio_url, audio_size_bytes


def _extract_content_notebooks(content: Optional[str]) -> List[str]:
    """Best-effort extraction of which notebooks fed an episode's content.

    PodcastEpisode has no structured "sources used" field - the generation
    dialog (GeneratePodcastDialog.buildContentFromSelections) flattens the
    user's source/note selection into one big text blob per notebook, each
    headed by a line from common.notebookLabel ("Notebook: {{name}}" /
    "Cuaderno: {{name}}" - the only two locales that render this label with
    real, non-English-fallback text, see frontend/src/lib/locales). This
    recovers just the notebook names from that header line without changing
    the storage format or the generation pipeline (commands/podcast_commands.py)
    - intentionally scoped down from a structured source-id list, which would
    need a schema/pipeline change in code outside this agent's scope. Legacy
    episodes generated before this existed, or ones whose content didn't use
    that header, simply report no content_notebooks (never an error).
    """
    if not content:
        return []
    seen: List[str] = []
    for match in _NOTEBOOK_LABEL_PATTERN.finditer(content):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


@router.get("/podcasts/estimate", response_model=PodcastGenerationEstimateResponse)
async def estimate_podcast_generation(
    episode_profile: str,
    speaker_profile: str,
    estimated_input_tokens: Optional[int] = None,
):
    """
    Best-effort cost/usage estimate for a podcast generation, intended to be
    shown to the user BEFORE they confirm the actual `/podcasts/generate`
    call (P1 "Podcasts y audio" - explicit cost estimate before generation).

    Returns the provider/model that will be used for each generation stage
    (outline, transcript, TTS) and a price where one is known - reusing the
    same versioned price table / free-rule / OpenRouter-catalog lookup the
    Cost Meter (usage_event) uses, so the number is consistent with what
    actually gets billed afterwards. An unpriced model is reported as
    cost_status="unknown" with estimated_cost_usd=None, never a fake $0.
    """
    try:
        return await PodcastService.estimate_generation(
            episode_profile_name=episode_profile,
            speaker_profile_name=speaker_profile,
            estimated_input_tokens=estimated_input_tokens,
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error estimating podcast generation cost: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to estimate podcast generation cost"
        )


@router.post("/podcasts/generate", response_model=PodcastGenerationResponse)
async def generate_podcast(request: PodcastGenerationRequest):
    """
    Generate a podcast episode using Episode Profiles.
    Returns immediately with job ID for status tracking.
    """
    try:
        job_id = await PodcastService.submit_generation_job(
            episode_profile_name=request.episode_profile,
            speaker_profile_name=request.speaker_profile,
            episode_name=request.episode_name,
            notebook_id=request.notebook_id,
            content=request.content,
            briefing_suffix=request.briefing_suffix,
        )

        return PodcastGenerationResponse(
            job_id=job_id,
            status="submitted",
            message=f"Podcast generation started for episode '{request.episode_name}'",
            episode_profile=request.episode_profile,
            episode_name=request.episode_name,
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error generating podcast: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to generate podcast"
        )


@router.get("/podcasts/jobs/{job_id}")
async def get_podcast_job_status(job_id: str):
    """Get the status of a podcast generation job"""
    try:
        status_data = await PodcastService.get_job_status(job_id)
        return status_data

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast job status: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to fetch job status"
        )


@router.get("/podcasts/episodes", response_model=List[PodcastEpisodeResponse])
async def list_podcast_episodes():
    """List all podcast episodes"""
    try:
        episodes = await PodcastService.list_episodes()

        # Batch-fetch job status for every episode with a command in one
        # query instead of one round trip per episode (see
        # PodcastEpisode.get_job_details_for_commands docstring).
        try:
            details_by_command = await PodcastEpisode.get_job_details_for_commands(
                [episode.command for episode in episodes if episode.command]
            )
        except Exception as e:
            logger.warning(f"Error batch-fetching podcast job statuses: {str(e)}")
            details_by_command = {}

        # Batch-resolve the snapshots' model references (outline_llm,
        # transcript_llm, voice_model) to display fields in one query
        # instead of one lookup per episode.
        models_by_id = await _resolve_snapshot_models(episodes)

        response_episodes = []
        for episode in episodes:
            # Skip incomplete episodes without command or audio
            if not episode.command and not episode.audio_file:
                continue

            # Get job status and error message if available
            job_status = None
            error_message = None
            if episode.command:
                detail = details_by_command.get(str(episode.command))
                if detail is not None:
                    job_status = normalize_job_status(detail["status"])
                    error_message = sanitize_job_error(detail["error_message"])
                else:
                    job_status = "unknown"
            else:
                # No command but has audio file = completed import
                job_status = "completed"

            audio_url, audio_size_bytes = _resolve_audio_url_and_size(episode)

            response_episodes.append(
                PodcastEpisodeResponse(
                    id=str(episode.id),
                    name=episode.name,
                    episode_profile=_with_resolved_model_fields(
                        episode.episode_profile,
                        _EPISODE_PROFILE_MODEL_FIELDS,
                        models_by_id,
                    ),
                    speaker_profile=_with_resolved_model_fields(
                        episode.speaker_profile,
                        _SPEAKER_PROFILE_MODEL_FIELDS,
                        models_by_id,
                    ),
                    briefing=episode.briefing,
                    audio_file=episode.audio_file,
                    audio_url=audio_url,
                    audio_size_bytes=audio_size_bytes,
                    transcript=episode.transcript,
                    outline=episode.outline,
                    created=str(episode.created) if episode.created else None,
                    job_status=job_status,
                    error_message=error_message,
                    command_id=str(episode.command) if episode.command else None,
                    favorited=bool(episode.favorited),
                    tags=list(episode.tags or []),
                    content_notebooks=_extract_content_notebooks(episode.content),
                )
            )

        return response_episodes

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error listing podcast episodes: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to list podcast episodes"
        )


@router.get("/podcasts/episodes/{episode_id}", response_model=PodcastEpisodeResponse)
async def get_podcast_episode(episode_id: str):
    """Get a specific podcast episode"""
    try:
        episode = await PodcastService.get_episode(episode_id)

        # Get job status and error message if available
        job_status = None
        error_message = None
        if episode.command:
            try:
                detail = await episode.get_job_detail()
                job_status = normalize_job_status(detail["status"])
                error_message = sanitize_job_error(detail["error_message"])
            except Exception:
                job_status = "unknown"
        else:
            # No command but has audio file = completed import
            job_status = "completed" if episode.audio_file else "unknown"

        audio_url, audio_size_bytes = _resolve_audio_url_and_size(episode)

        models_by_id = await _resolve_snapshot_models([episode])

        return PodcastEpisodeResponse(
            id=str(episode.id),
            name=episode.name,
            episode_profile=_with_resolved_model_fields(
                episode.episode_profile,
                _EPISODE_PROFILE_MODEL_FIELDS,
                models_by_id,
            ),
            speaker_profile=_with_resolved_model_fields(
                episode.speaker_profile,
                _SPEAKER_PROFILE_MODEL_FIELDS,
                models_by_id,
            ),
            briefing=episode.briefing,
            audio_file=episode.audio_file,
            audio_url=audio_url,
            audio_size_bytes=audio_size_bytes,
            transcript=episode.transcript,
            outline=episode.outline,
            created=str(episode.created) if episode.created else None,
            job_status=job_status,
            error_message=error_message,
            command_id=str(episode.command) if episode.command else None,
            favorited=bool(episode.favorited),
            tags=list(episode.tags or []),
            content_notebooks=_extract_content_notebooks(episode.content),
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast episode: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")


@router.patch("/podcasts/episodes/{episode_id}", response_model=PodcastEpisodeResponse)
async def update_podcast_episode_organization(
    episode_id: str, update: PodcastEpisodeOrganizationUpdate
):
    """Update an episode's favorite flag and/or tags (P1 "Organizacion y
    busqueda" - minimal organization metadata). Only favorited/tags are
    mutable; the generated content/profile snapshots are immutable."""
    try:
        episode = await PodcastService.get_episode(episode_id)

        if update.favorited is not None:
            episode.favorited = update.favorited
        if update.tags is not None:
            # Dedupe + drop blanks, same light normalization as the frontend
            # tag input - keeps the stored list honest without a taxonomy.
            seen: List[str] = []
            for tag in update.tags:
                cleaned = tag.strip()
                if cleaned and cleaned not in seen:
                    seen.append(cleaned)
            episode.tags = seen

        await episode.save()

        return await get_podcast_episode(str(episode.id))

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating podcast episode organization: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to update episode"
        )


@router.get("/podcasts/episodes/{episode_id}/audio")
async def stream_podcast_episode_audio(episode_id: str):
    """Stream the audio file associated with a podcast episode"""
    try:
        episode = await PodcastService.get_episode(episode_id)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast episode for audio: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.audio_file:
        raise HTTPException(status_code=404, detail="Episode has no audio file")

    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is None:
        logger.warning(
            f"Blocked audio access outside podcasts directory for episode "
            f"{episode_id}: {episode.audio_file}"
        )
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=audio_path.name,
    )


@router.post("/podcasts/episodes/{episode_id}/retry")
async def retry_podcast_episode(episode_id: str):
    """Retry a failed podcast episode by deleting it and submitting a new job"""
    try:
        episode = await PodcastService.get_episode(episode_id)

        # Validate episode is in a failed state
        detail = await episode.get_job_detail()
        current_status = normalize_job_status(detail["status"])
        if current_status not in ("failed", "canceled"):
            raise HTTPException(
                status_code=400,
                detail=f"Episode is not in a failed state (current: {current_status})",
            )

        # Extract params for re-submission
        ep_profile_name = episode.episode_profile.get("name")
        sp_profile_name = episode.speaker_profile.get("name")
        episode_name = episode.name
        content = episode.content

        if not ep_profile_name or not sp_profile_name:
            raise HTTPException(
                status_code=400,
                detail="Cannot retry: episode or speaker profile name missing from stored data",
            )

        # Delete audio file if any
        _delete_episode_audio(episode, episode_id)

        # Delete the failed episode
        await episode.delete()

        # Submit a new job
        job_id = await PodcastService.submit_generation_job(
            episode_profile_name=ep_profile_name,
            speaker_profile_name=sp_profile_name,
            episode_name=episode_name,
            content=content,
        )

        return {"job_id": job_id, "message": "Retry submitted successfully"}

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error retrying podcast episode: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retry episode"
        )


@router.delete("/podcasts/episodes/{episode_id}")
async def delete_podcast_episode(episode_id: str):
    """Delete a podcast episode and its associated audio file"""
    try:
        # Get the episode first to check if it exists and get the audio file path
        episode = await PodcastService.get_episode(episode_id)

        # Delete the physical audio file if it exists
        _delete_episode_audio(episode, episode_id)

        # Delete the episode from the database
        await episode.delete()

        logger.info(f"Deleted podcast episode: {episode_id}")
        return {"message": "Episode deleted successfully", "episode_id": episode_id}

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting podcast episode: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to delete episode"
        )
