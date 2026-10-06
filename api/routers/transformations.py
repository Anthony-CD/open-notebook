import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from ai_prompter import Prompter
from fastapi import APIRouter, HTTPException
from loguru import logger

from api.models import (
    DefaultPromptResponse,
    DefaultPromptUpdate,
    TransformationCostEstimateRequest,
    TransformationCostEstimateResponse,
    TransformationCreate,
    TransformationExecuteRequest,
    TransformationExecuteResponse,
    TransformationPreviewRequest,
    TransformationPreviewResponse,
    TransformationResponse,
    TransformationResultHistoryResponse,
    TransformationResultVersionResponse,
    TransformationUpdate,
)
from open_notebook.ai.models import DefaultModels, Model
from open_notebook.ai.pricing import compute_cost, lookup_price
from open_notebook.domain.notebook import Source
from open_notebook.domain.transformation import DefaultPrompts, Transformation
from open_notebook.domain.usage import UsageEvent
from open_notebook.exceptions import InvalidInputError, OpenNotebookError
from open_notebook.graphs.transformation import graph as transformation_graph
from open_notebook.utils import token_count

router = APIRouter()

# Rough estimate for the LLM response length, used only for the pre-execution
# cost ESTIMATE (open_notebook/ai/pricing.py's get_price_per_token). The real
# output token count - and therefore the real cost - is only known after the
# call completes (see TransformationExecuteResponse.actual_cost_usd), because
# a transformation's output length depends on both the source content and
# what the prompt asks for.
_ESTIMATED_OUTPUT_TOKENS = 1024

# In-process guard against re-triggering the exact same transformation run
# (same transformation + model + input text) while one is already in
# flight - a simple membership set is enough for a single-process FastAPI
# app (see AGENTS.md: "dev defaults, not production hardening"). This is
# NOT a substitute for queue-based cancellation/dedup: /transformations/
# execute is a synchronous preview/test harness (no source, no persistence -
# see run_transformation below), unlike the already-queued, surreal_commands-
# backed POST /sources/{source_id}/insights endpoint (api/routers/
# sources.py), which submits the "run_transformation" command
# (commands/source_commands.py) and is out of this file's scope.
_IN_FLIGHT_EXECUTIONS: set[str] = set()


def _execution_key(transformation_id: str, model_id: Optional[str], input_text: str) -> str:
    digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    return f"{transformation_id}:{model_id or ''}:{digest}"


async def _effective_model_id(transformation: Transformation, model_id: Optional[str]) -> Optional[str]:
    """Resolve the model that execution would actually use, best-effort.

    Mirrors the precedence provision_langchain_model() applies for the
    "transformation" task type (explicit > transformation default > system
    default), without the large-context auto-upgrade (not knowable ahead of
    a real token count) - good enough for a pre-execution estimate.
    """
    if model_id:
        return model_id
    if transformation.model_id:
        return transformation.model_id
    try:
        defaults: DefaultModels = await DefaultModels.get_instance()  # type: ignore[assignment]
        return defaults.default_transformation_model or defaults.default_chat_model
    except Exception as e:
        logger.warning(f"Could not resolve default transformation model: {e}")
        return None


def _transformation_response(transformation: Transformation) -> TransformationResponse:
    return TransformationResponse(
        id=transformation.id or "",
        name=transformation.name,
        title=transformation.title,
        description=transformation.description,
        prompt=transformation.prompt,
        apply_default=transformation.apply_default,
        model_id=transformation.model_id,
        created=str(transformation.created),
        updated=str(transformation.updated),
    )


@router.get("/transformations", response_model=List[TransformationResponse])
async def get_transformations():
    """Get all transformations."""
    try:
        transformations = await Transformation.get_all(order_by="name asc")

        return [
            _transformation_response(transformation)
            for transformation in transformations
        ]
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching transformations: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching transformations: {str(e)}"
        )


@router.post("/transformations", response_model=TransformationResponse)
async def create_transformation(transformation_data: TransformationCreate):
    """Create a new transformation."""
    try:
        # Reject unknown model references up front (same check as execute);
        # otherwise an invalid model_id is stored and only fails at run time.
        if transformation_data.model_id:
            model = await Model.get(transformation_data.model_id)
            if not model:
                raise HTTPException(status_code=404, detail="Model not found")

        new_transformation = Transformation(
            name=transformation_data.name,
            title=transformation_data.title,
            description=transformation_data.description,
            prompt=transformation_data.prompt,
            apply_default=transformation_data.apply_default,
            model_id=transformation_data.model_id,
        )
        await new_transformation.save()

        return _transformation_response(new_transformation)
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating transformation: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating transformation: {str(e)}"
        )


@router.post("/transformations/execute", response_model=TransformationExecuteResponse)
async def execute_transformation(execute_request: TransformationExecuteRequest):
    """Execute a transformation on input text.

    This is a synchronous preview/test harness (no source is attached, so
    nothing is persisted) - see the module docstring note on
    _IN_FLIGHT_EXECUTIONS for why a real queued job/cancellation system
    lives elsewhere. Guards against re-submitting the exact same
    (transformation, model, input text) while one is already running, and
    reports the real post-execution token usage/cost recorded by
    provision_langchain_model()'s usage-tracking callback.
    """
    key = _execution_key(
        execute_request.transformation_id,
        execute_request.model_id,
        execute_request.input_text,
    )
    if key in _IN_FLIGHT_EXECUTIONS:
        raise HTTPException(
            status_code=409,
            detail="This transformation is already running for this input. Please wait for it to finish.",
        )
    _IN_FLIGHT_EXECUTIONS.add(key)
    try:
        # Validate transformation exists
        transformation = await Transformation.get(execute_request.transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        model_id = execute_request.model_id or transformation.model_id

        # Validate explicit or transformation-specific model exists.
        # None is allowed so the graph can use the configured transformation default.
        if model_id:
            model = await Model.get(model_id)
            if not model:
                raise HTTPException(status_code=404, detail="Model not found")

        start_time = datetime.now(timezone.utc)

        # Execute the transformation.
        # LangGraph accepts a partial state dict at runtime, but its typed
        # overloads require the full state type (langgraph typing limitation).
        result = await transformation_graph.ainvoke(  # type: ignore[call-overload]
            dict(
                input_text=execute_request.input_text,
                transformation=transformation,
            ),
            config=dict(configurable={"model_id": model_id}),
        )

        response = TransformationExecuteResponse(
            output=result["output"],
            transformation_id=execute_request.transformation_id,
            model_id=model_id,
        )

        # Best-effort: attach the real cost recorded by the usage-tracking
        # callback provision_langchain_model() attaches to every call (see
        # open_notebook/ai/provision.py). Never raises/blocks the response -
        # a lookup failure just means the usage fields stay None.
        try:
            rows = await UsageEvent.fetch_rows(start_time, None)
            transformation_rows = [r for r in rows if r.get("task_type") == "transformation"]
            if transformation_rows:
                latest = transformation_rows[-1]
                response.input_tokens = latest.get("input_tokens")
                response.output_tokens = latest.get("output_tokens")
                response.cost_status = latest.get("cost_status")
                response.actual_cost_usd = latest.get("estimated_cost_usd")
        except Exception as e:
            logger.warning(f"Could not attach real cost to transformation execution response: {e}")

        return response

    except HTTPException:
        raise
    except OpenNotebookError:
        raise  # Let global exception handlers return proper status codes
    except Exception as e:
        logger.error(f"Error executing transformation: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error executing transformation: {str(e)}"
        )
    finally:
        _IN_FLIGHT_EXECUTIONS.discard(key)


@router.post("/transformations/preview", response_model=TransformationPreviewResponse)
async def preview_transformation(preview_request: TransformationPreviewRequest):
    """Render the exact prompt a transformation will send, without calling an LLM.

    Lets the user see the real compiled instructions before saving a
    transformation (api/routers/transformations.py scope, item 1 of the P1
    "Transformaciones" work). Source content is never interpolated into the
    system prompt (see prompts/transformation/execute.jinja) - it is always
    sent as a separate user message, so this returns both parts.
    """
    try:
        instructions = preview_request.prompt
        default_prompts: DefaultPrompts = await DefaultPrompts.get_instance()  # type: ignore[assignment]
        if default_prompts.transformation_instructions:
            instructions = (
                f"{default_prompts.transformation_instructions}\n\n{instructions}"
            )

        system_prompt = Prompter(prompt_template="transformation/execute").render(
            data={"instructions": instructions}
        )

        sample_input = preview_request.sample_text or (
            "[This is a placeholder. The actual source content (or pasted "
            "text) is sent here, as a separate message - it is never "
            "inserted into the system prompt above.]"
        )

        return TransformationPreviewResponse(
            system_prompt=system_prompt,
            sample_input=sample_input,
        )
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error previewing transformation prompt: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error previewing transformation prompt: {str(e)}"
        )


@router.post(
    "/transformations/estimate-cost", response_model=TransformationCostEstimateResponse
)
async def estimate_transformation_cost(estimate_request: TransformationCostEstimateRequest):
    """Best-effort cost ESTIMATE for running a transformation, before running it.

    Never invents a $0 cost: cost_status is 'known' | 'free' | 'unknown', and
    estimated_cost_usd is None whenever the model's price isn't known (the
    frontend shows "Costo no disponible" for that case, matching the Cost
    Meter's existing convention - see open_notebook/ai/pricing.py).
    Real cost is only known after execution (actual token counts aren't
    knowable ahead of the call) - see TransformationExecuteResponse.
    """
    try:
        transformation = await Transformation.get(estimate_request.transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        effective_model_id = await _effective_model_id(
            transformation, estimate_request.model_id
        )

        provider: Optional[str] = None
        model_name: Optional[str] = None
        if effective_model_id:
            model = await Model.get(effective_model_id)
            if model:
                provider = model.provider
                model_name = model.name

        instructions = transformation.prompt
        default_prompts: DefaultPrompts = await DefaultPrompts.get_instance()  # type: ignore[assignment]
        if default_prompts.transformation_instructions:
            instructions = (
                f"{default_prompts.transformation_instructions}\n\n{instructions}"
            )
        estimated_input_tokens = token_count(instructions) + token_count(
            estimate_request.input_text
        )

        if provider and model_name:
            price = await lookup_price(provider, model_name)
            cost = compute_cost(price, estimated_input_tokens, _ESTIMATED_OUTPUT_TOKENS)
            cost_status = price.status if cost is not None else "unknown"
        else:
            cost, cost_status = None, "unknown"

        return TransformationCostEstimateResponse(
            model_id=effective_model_id,
            provider=provider,
            model_name=model_name,
            cost_status=cost_status,
            estimated_cost_usd=cost,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=_ESTIMATED_OUTPUT_TOKENS,
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error estimating transformation cost: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error estimating transformation cost: {str(e)}"
        )


@router.get(
    "/transformations/{transformation_id}/results/{source_id}",
    response_model=TransformationResultHistoryResponse,
)
async def get_transformation_result_history(transformation_id: str, source_id: str):
    """Version history of a transformation's results on one source.

    Re-running a transformation never overwrote prior results (each run
    creates a new source_insight row - see commands/embedding_commands.py's
    create_insight_command); this surfaces that history with a version
    number per result (oldest = 1) instead of a flat, undifferentiated list.
    See Transformation.get_result_versions() for how version numbers are
    computed/resolved (open_notebook/domain/transformation.py).
    """
    try:
        transformation = await Transformation.get(transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        versions = await transformation.get_result_versions(source_id)

        return TransformationResultHistoryResponse(
            transformation_id=transformation_id,
            source_id=source_id,
            versions=[TransformationResultVersionResponse(**v) for v in versions],
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(
            f"Error fetching result history for transformation {transformation_id} "
            f"on source {source_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Error fetching transformation result history"
        )


@router.get("/transformations/default-prompt", response_model=DefaultPromptResponse)
async def get_default_prompt():
    """Get the default transformation prompt."""
    try:
        default_prompts: DefaultPrompts = await DefaultPrompts.get_instance()  # type: ignore[assignment]

        return DefaultPromptResponse(
            transformation_instructions=default_prompts.transformation_instructions
            or ""
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching default prompt: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching default prompt: {str(e)}"
        )


@router.put("/transformations/default-prompt", response_model=DefaultPromptResponse)
async def update_default_prompt(prompt_update: DefaultPromptUpdate):
    """Update the default transformation prompt."""
    try:
        default_prompts: DefaultPrompts = await DefaultPrompts.get_instance()  # type: ignore[assignment]

        default_prompts.transformation_instructions = (
            prompt_update.transformation_instructions
        )
        await default_prompts.update()

        return DefaultPromptResponse(
            transformation_instructions=default_prompts.transformation_instructions
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating default prompt: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error updating default prompt: {str(e)}"
        )


@router.get(
    "/transformations/{transformation_id}", response_model=TransformationResponse
)
async def get_transformation(transformation_id: str):
    """Get a specific transformation by ID."""
    try:
        transformation = await Transformation.get(transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        return _transformation_response(transformation)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching transformation {transformation_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching transformation: {str(e)}"
        )


@router.put(
    "/transformations/{transformation_id}", response_model=TransformationResponse
)
async def update_transformation(
    transformation_id: str, transformation_update: TransformationUpdate
):
    """Update a transformation."""
    try:
        transformation = await Transformation.get(transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        # Update only provided fields
        if transformation_update.name is not None:
            transformation.name = transformation_update.name
        if transformation_update.title is not None:
            transformation.title = transformation_update.title
        if transformation_update.description is not None:
            transformation.description = transformation_update.description
        if transformation_update.prompt is not None:
            transformation.prompt = transformation_update.prompt
        if transformation_update.apply_default is not None:
            transformation.apply_default = transformation_update.apply_default
        if "model_id" in transformation_update.model_fields_set:
            # Validate a newly supplied model reference (allow clearing to None).
            if transformation_update.model_id:
                model = await Model.get(transformation_update.model_id)
                if not model:
                    raise HTTPException(status_code=404, detail="Model not found")
            transformation.model_id = transformation_update.model_id

        await transformation.save()

        return _transformation_response(transformation)
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating transformation {transformation_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error updating transformation: {str(e)}"
        )


@router.delete("/transformations/{transformation_id}")
async def delete_transformation(transformation_id: str):
    """Delete a transformation."""
    try:
        transformation = await Transformation.get(transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        await transformation.delete()

        return {"message": "Transformation deleted successfully"}
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting transformation {transformation_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting transformation: {str(e)}"
        )
