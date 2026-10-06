from typing import Any, Dict, Optional

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel
from surreal_commands import get_command_status, submit_command

from open_notebook.ai.models import Model
from open_notebook.ai.pricing import compute_cost, lookup_price
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import NotFoundError
from open_notebook.podcasts.models import EpisodeProfile, PodcastEpisode, SpeakerProfile
from open_notebook.utils.job_status import normalize_job_status, sanitize_job_error


class PodcastGenerationRequest(BaseModel):
    """Request model for podcast generation"""

    episode_profile: str
    speaker_profile: str
    episode_name: str
    content: Optional[str] = None
    notebook_id: Optional[str] = None
    briefing_suffix: Optional[str] = None


class PodcastGenerationResponse(BaseModel):
    """Response model for podcast generation"""

    job_id: str
    status: str
    message: str
    episode_profile: str
    episode_name: str


class PodcastStageEstimate(BaseModel):
    """Best-effort cost estimate for one generation stage (outline/transcript/tts).

    cost_status mirrors open_notebook.ai.pricing.CostStatus / usage_event's
    convention: 'known' | 'free' | 'unknown'. estimated_cost_usd is only
    populated for 'known' (and is 0.0 for 'free') - an 'unknown' price is
    NEVER reported as a fake $0 (see pricing.py module docstring).
    """

    role: str
    provider: Optional[str] = None
    model_name: Optional[str] = None
    cost_status: str = "unknown"
    estimated_cost_usd: Optional[float] = None
    pricing_version: Optional[str] = None
    configured: bool = True


class PodcastGenerationEstimateResponse(BaseModel):
    """Response for GET /podcasts/estimate - shown to the user BEFORE they
    confirm generation (see api/routers/podcasts.py docstring)."""

    outline: PodcastStageEstimate
    transcript: PodcastStageEstimate
    tts: PodcastStageEstimate
    total_estimated_cost_usd: Optional[float] = None
    is_complete: bool = False
    is_estimate: bool = True


# Rough per-stage token multipliers for the cost estimate. Podcast generation
# has no token-accounting hook of its own (provision_langchain_model()'s
# usage_event callback only fires when the LLM is actually called - by
# definition too late for a pre-generation estimate), so this approximates
# input/output tokens from the content size and the profile's segment count.
# These are deliberately rough and labeled as an estimate everywhere they
# surface - never presented as an exact cost.
_OUTPUT_TOKENS_PER_SEGMENT_OUTLINE = 200
_OUTPUT_TOKENS_PER_SEGMENT_TRANSCRIPT = 600
# The transcript stage reads the content again plus the generated outline.
_TRANSCRIPT_INPUT_OVERHEAD_TOKENS = 500


async def _estimate_stage(
    role: str, model_id: Optional[str]
) -> PodcastStageEstimate:
    """Resolve a profile's model reference to a priced estimate stub (no
    token numbers yet - compute_cost() is applied by the caller, which knows
    the per-stage input/output token estimate)."""
    if not model_id:
        return PodcastStageEstimate(role=role, configured=False, cost_status="unknown")
    try:
        model = await Model.get(model_id)
    except Exception as e:
        logger.warning(f"Cost estimate: could not resolve model {model_id}: {e}")
        return PodcastStageEstimate(role=role, configured=False, cost_status="unknown")

    price = await lookup_price(model.provider, model.name)
    return PodcastStageEstimate(
        role=role,
        provider=model.provider,
        model_name=model.name,
        cost_status=price.status,
        pricing_version=price.pricing_version,
    )


class PodcastService:
    """Service layer for podcast operations"""

    @staticmethod
    async def submit_generation_job(
        episode_profile_name: str,
        speaker_profile_name: str,
        episode_name: str,
        notebook_id: Optional[str] = None,
        content: Optional[str] = None,
        briefing_suffix: Optional[str] = None,
    ) -> str:
        """Submit a podcast generation job for background processing"""
        try:
            # Validate episode profile exists
            episode_profile = await EpisodeProfile.get_by_name(episode_profile_name)
            if not episode_profile:
                raise ValueError(f"Episode profile '{episode_profile_name}' not found")

            # Resolve the user-facing speaker profile name to a record ID at
            # the API boundary (#630) - everything downstream works with IDs.
            speaker_profile = await SpeakerProfile.resolve(speaker_profile_name)
            if not speaker_profile:
                raise ValueError(f"Speaker profile '{speaker_profile_name}' not found")

            # Get content from notebook if not provided directly
            if not content and notebook_id:
                try:
                    notebook = await Notebook.get(notebook_id)
                    # Get notebook context (this may need to be adjusted based on actual Notebook implementation)
                    content = (
                        await notebook.get_context()
                        if hasattr(notebook, "get_context")
                        else str(notebook)
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to get notebook content, using notebook_id as content: {e}"
                    )
                    content = f"Notebook ID: {notebook_id}"

            if not content:
                raise ValueError(
                    "Content is required - provide either content or notebook_id"
                )

            # Prepare command arguments (speaker profile as record ID)
            command_args = {
                "episode_profile": episode_profile_name,
                "speaker_profile": str(speaker_profile.id),
                "episode_name": episode_name,
                "content": str(content),
                "briefing_suffix": briefing_suffix,
            }

            # Ensure command modules are imported before submitting
            # This is needed because submit_command validates against local registry
            try:
                import commands.podcast_commands  # noqa: F401
            except ImportError as import_err:
                logger.error(f"Failed to import podcast commands: {import_err}")
                raise ValueError("Podcast commands not available")

            # Submit command to surreal-commands
            job_id = submit_command("open_notebook", "generate_podcast", command_args)

            # Convert RecordID to string if needed
            if not job_id:
                raise ValueError("Failed to get job_id from submit_command")
            job_id_str = str(job_id)
            logger.info(
                f"Submitted podcast generation job: {job_id_str} for episode '{episode_name}'"
            )
            return job_id_str

        except Exception as e:
            logger.error(f"Failed to submit podcast generation job: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to submit podcast generation job",
            )

    @staticmethod
    async def get_job_status(job_id: str) -> Dict[str, Any]:
        """Get status of a podcast generation job"""
        try:
            status = await get_command_status(job_id)
            return {
                "job_id": job_id,
                "status": normalize_job_status(status.status if status else None),
                "result": status.result if status else None,
                "error_message": sanitize_job_error(
                    getattr(status, "error_message", None) if status else None
                ),
                "created": str(status.created)
                if status and hasattr(status, "created") and status.created
                else None,
                "updated": str(status.updated)
                if status and hasattr(status, "updated") and status.updated
                else None,
                "progress": getattr(status, "progress", None) if status else None,
            }
        except Exception as e:
            logger.error(f"Failed to get podcast job status: {e}")
            raise HTTPException(status_code=500, detail="Failed to get job status")

    @staticmethod
    async def list_episodes() -> list:
        """List all podcast episodes"""
        try:
            episodes = await PodcastEpisode.get_all(order_by="created desc")
            return episodes
        except Exception as e:
            logger.error(f"Failed to list podcast episodes: {e}")
            raise HTTPException(status_code=500, detail="Failed to list episodes")

    @staticmethod
    async def get_episode(episode_id: str) -> PodcastEpisode:
        """Get a specific podcast episode"""
        try:
            episode = await PodcastEpisode.get(episode_id)
            return episode
        except Exception as e:
            logger.error(f"Failed to get podcast episode {episode_id}: {e}")
            raise HTTPException(status_code=404, detail="Episode not found")

    @staticmethod
    async def estimate_generation(
        episode_profile_name: str,
        speaker_profile_name: str,
        estimated_input_tokens: Optional[int] = None,
    ) -> PodcastGenerationEstimateResponse:
        """Best-effort cost estimate for generating an episode, BEFORE the job
        is submitted - see api/routers/podcasts.py's /podcasts/estimate.

        Resolves the three models the pipeline will call (outline_llm,
        transcript_llm, voice_model) and prices them via
        open_notebook.ai.pricing - the same versioned table/free-rule/
        OpenRouter-catalog lookup usage_event accounting uses, so an episode
        profile pointing at a model with a known price gets a real number
        and everything else is honestly reported as "unknown" (never a fake
        $0 - see pricing.py).

        `estimated_input_tokens` is the caller's own best estimate of the
        selected content's size (the frontend already computes this via
        chatApi.buildContext for the generation dialog's token counter) - when
        omitted, stage costs for LLM roles stay "unknown" rather than
        guessing a content size out of thin air.
        """
        episode_profile = await EpisodeProfile.get_by_name(episode_profile_name)
        if not episode_profile:
            raise NotFoundError(f"Episode profile '{episode_profile_name}' not found")

        speaker_profile = await SpeakerProfile.resolve(speaker_profile_name)
        if not speaker_profile:
            raise NotFoundError(f"Speaker profile '{speaker_profile_name}' not found")

        outline = await _estimate_stage("outline", episode_profile.outline_llm)
        transcript = await _estimate_stage("transcript", episode_profile.transcript_llm)
        tts = await _estimate_stage("tts", speaker_profile.voice_model)

        num_segments = episode_profile.num_segments or 5

        if outline.cost_status == "known" and estimated_input_tokens is not None:
            price = await lookup_price(outline.provider, outline.model_name)
            outline.estimated_cost_usd = compute_cost(
                price,
                input_tokens=estimated_input_tokens,
                output_tokens=num_segments * _OUTPUT_TOKENS_PER_SEGMENT_OUTLINE,
            )
        elif outline.cost_status == "free":
            outline.estimated_cost_usd = 0.0

        if transcript.cost_status == "known" and estimated_input_tokens is not None:
            price = await lookup_price(transcript.provider, transcript.model_name)
            transcript.estimated_cost_usd = compute_cost(
                price,
                input_tokens=estimated_input_tokens + _TRANSCRIPT_INPUT_OVERHEAD_TOKENS,
                output_tokens=num_segments * _OUTPUT_TOKENS_PER_SEGMENT_TRANSCRIPT,
            )
        elif transcript.cost_status == "free":
            transcript.estimated_cost_usd = 0.0

        # TTS is priced per character/minute by most providers, not per
        # text-LLM token - lookup_price() only has entries for a handful of
        # direct per-token providers, so this is "free" when the voice model
        # is on a free runtime/endpoint and "unknown" otherwise. That is the
        # honest answer, not a guess: see pricing.py's "Costo no disponible"
        # convention (open_notebook/domain/usage.py / the Cost Meter UI).
        if tts.cost_status == "free":
            tts.estimated_cost_usd = 0.0

        stages = [outline, transcript, tts]
        known_costs = [s.estimated_cost_usd for s in stages if s.estimated_cost_usd is not None]
        is_complete = all(s.cost_status in ("known", "free") for s in stages)
        total = sum(known_costs) if known_costs else None

        return PodcastGenerationEstimateResponse(
            outline=outline,
            transcript=transcript,
            tts=tts,
            total_estimated_cost_usd=round(total, 10) if total is not None else None,
            is_complete=is_complete,
        )


class DefaultProfiles:
    """Utility class for creating default profiles (if needed beyond migration data)"""

    @staticmethod
    async def create_default_episode_profiles():
        """Create default episode profiles if they don't exist"""
        try:
            # Check if profiles already exist
            existing = await EpisodeProfile.get_all()
            if existing:
                logger.info(f"Episode profiles already exist: {len(existing)} found")
                return existing

            # This would create profiles, but since we have migration data,
            # this is mainly for future extensibility
            logger.info(
                "Default episode profiles should be created via database migration"
            )
            return []

        except Exception as e:
            logger.error(f"Failed to create default episode profiles: {e}")
            raise

    @staticmethod
    async def create_default_speaker_profiles():
        """Create default speaker profiles if they don't exist"""
        try:
            # Check if profiles already exist
            existing = await SpeakerProfile.get_all()
            if existing:
                logger.info(f"Speaker profiles already exist: {len(existing)} found")
                return existing

            # This would create profiles, but since we have migration data,
            # this is mainly for future extensibility
            logger.info(
                "Default speaker profiles should be created via database migration"
            )
            return []

        except Exception as e:
            logger.error(f"Failed to create default speaker profiles: {e}")
            raise
