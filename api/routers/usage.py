from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.models import (
    BudgetCheckRequest,
    BudgetCheckResponse,
    BudgetSettingsUpdate,
    BudgetStatusResponse,
    UsageSummaryResponse,
)
from api.usage_service import (
    check_expensive_operation,
    get_budget_settings_and_status,
    get_usage_summary,
    update_budget_settings,
)
from open_notebook.exceptions import InvalidInputError, OpenNotebookError

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


@router.get("/usage/budget", response_model=BudgetStatusResponse)
async def usage_budget():
    """Configured daily/monthly budgets and current known spend vs each."""
    try:
        return BudgetStatusResponse(**await get_budget_settings_and_status())
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching budget status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch budget status")


@router.put("/usage/budget", response_model=BudgetStatusResponse)
async def update_usage_budget(payload: BudgetSettingsUpdate):
    """Update the user-configured daily/monthly LLM spend limits."""
    try:
        return BudgetStatusResponse(
            **await update_budget_settings(
                daily_budget_usd=payload.daily_budget_usd,
                monthly_budget_usd=payload.monthly_budget_usd,
            )
        )
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating budget settings: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update budget settings")


@router.post("/usage/budget/check", response_model=BudgetCheckResponse)
async def check_usage_budget(payload: BudgetCheckRequest):
    """Pre-expensive-operation check: estimate vs remaining daily/monthly budget.

    Never blocks by itself - always 200s with the estimate and current
    status so the caller can decide whether to confirm with the user before
    running a costly operation (podcast generation, a large transformation,
    study-set generation on a huge notebook).
    """
    try:
        result = await check_expensive_operation(payload.estimated_cost_usd)
        return BudgetCheckResponse(**result)
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error checking budget for operation: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check budget")
