from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.models import UsageSummaryResponse
from api.usage_service import get_usage_summary
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    period: Optional[str] = Query(
        "30d",
        description="Preset range: 7d | 30d | 365d | all (legacy: month, year). "
        "Ignored when `from`/`to` are given.",
    ),
    date_from: Optional[str] = Query(
        None, alias="from", description="ISO datetime start (e.g. session start)"
    ),
    date_to: Optional[str] = Query(None, alias="to", description="ISO datetime end"),
    model: Optional[str] = Query(None, description="Filter by model name"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    task_type: Optional[str] = Query(None, description="Filter by task type"),
):
    """Get aggregated LLM usage/cost from recorded usage events."""
    try:
        summary = await get_usage_summary(
            period=period,
            date_from=date_from,
            date_to=date_to,
            model=model,
            provider=provider,
            task_type=task_type,
        )
        return UsageSummaryResponse(**summary)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching usage summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch usage summary")
