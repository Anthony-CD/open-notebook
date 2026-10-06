from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Notebook models
class NotebookCreate(BaseModel):
    name: str = Field(..., description="Name of the notebook")
    description: str = Field(default="", description="Description of the notebook")


class NotebookUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Name of the notebook")
    description: Optional[str] = Field(None, description="Description of the notebook")
    archived: Optional[bool] = Field(
        None, description="Whether the notebook is archived"
    )


class NotebookResponse(BaseModel):
    id: str
    name: str
    description: str
    archived: bool
    created: str
    updated: str
    source_count: int
    note_count: int


class RecentlyViewedResponse(BaseModel):
    type: Literal["notebook", "source"]
    id: str
    title: str
    last_viewed_at: str


# Search models
class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    type: Literal["text", "vector"] = Field("text", description="Search type")
    limit: int = Field(100, description="Maximum number of results", ge=1, le=1000)
    search_sources: bool = Field(True, description="Include sources in search")
    search_notes: bool = Field(True, description="Include notes in search")
    search_chats: bool = Field(
        True,
        description=(
            "Include chat conversation messages. Unlike sources/notes this is "
            "always a plain case-insensitive substring match (chat history "
            "lives outside SurrealDB, in the LangGraph checkpoint store, with "
            "no full-text/vector index of its own) - not affected by `type`."
        ),
    )
    minimum_score: float = Field(
        0.2, description="Minimum score for vector search", ge=0, le=1
    )


class SearchResponse(BaseModel):
    results: List[Dict[str, Any]] = Field(..., description="Search results")
    total_count: int = Field(..., description="Total number of results")
    search_type: str = Field(..., description="Type of search performed")


class AskRequest(BaseModel):
    question: str = Field(..., description="Question to ask the knowledge base")
    strategy_model: str = Field(..., description="Model ID for query strategy")
    answer_model: str = Field(..., description="Model ID for individual answers")
    final_answer_model: str = Field(..., description="Model ID for final answer")


class Citation(BaseModel):
    """One structured citation backing a `[n]` marker in a chat/ask answer.

    Built from what was actually retrieved for the turn (never derived from
    the raw answer text), so the frontend can render a clickable chip without
    re-parsing the model's own output. `location` is `None` when the source
    type doesn't carry anything more precise than "the whole document" (no
    page/chunk tracking exists for notebook/source chat today).
    """

    number: int = Field(..., description="The [n] bracket number cited in the answer")
    ref_type: Literal["source", "source_insight", "note"] = Field(
        ..., description="What kind of record this citation points to"
    )
    ref_id: str = Field(..., description="Full record id, e.g. 'source:abc123'")
    title: str = Field(..., description="Display title of the cited item")
    location: Optional[str] = Field(
        None,
        description="Human-readable location within the item (e.g. 'Insight: summary'), when known",
    )
    snippet: str = Field(..., description="Short excerpt of the cited text")


class AskResponse(BaseModel):
    answer: str = Field(..., description="Final answer from the knowledge base")
    question: str = Field(..., description="Original question")
    citations: List[Citation] = Field(
        default_factory=list,
        description=(
            "Structured citations backing the [n] markers in `answer`. May be "
            "empty if the graph building this response hasn't been wired to "
            "populate it yet."
        ),
    )


# Models API models
class ModelCreate(BaseModel):
    name: str = Field(..., description="Model name (e.g., gpt-5-mini, claude, gemini)")
    provider: str = Field(
        ..., description="Provider name (e.g., openai, anthropic, gemini)"
    )
    type: str = Field(
        ...,
        description="Model type (language, embedding, text_to_speech, speech_to_text)",
    )
    credential: Optional[str] = Field(
        None, description="Credential ID to link this model to"
    )


class ModelResponse(BaseModel):
    id: str
    name: str
    provider: str
    type: str
    credential: Optional[str] = None
    created: str
    updated: str

    # Honest, best-effort metadata (P2 "Modelos, costo y confianza") - see
    # open_notebook/ai/model_metadata.py. context_window/capabilities are
    # None/[] when genuinely not known (most direct-provider models today),
    # never a fabricated guess. There is deliberately no "speed" field: no
    # provider or library in this codebase reports latency/throughput, so a
    # fast/standard/slow label would be invented, not measured.
    context_window: Optional[int] = Field(
        None, description="Token context window, when known (e.g. OpenRouter catalog)"
    )
    capabilities: List[str] = Field(
        default_factory=list,
        description="Known capability flags, e.g. ['vision', 'tools']. Empty when unknown.",
    )
    capabilities_known: bool = Field(
        False,
        description=(
            "Whether `capabilities` reflects real catalog data. False means "
            "'not tracked for this provider', not 'confirmed no capabilities'."
        ),
    )
    metadata_source: Optional[str] = Field(
        None, description="Where context_window/capabilities came from, e.g. 'openrouter-catalog'"
    )


class DefaultModelsResponse(BaseModel):
    default_chat_model: Optional[str] = None
    default_transformation_model: Optional[str] = None
    large_context_model: Optional[str] = None
    default_text_to_speech_model: Optional[str] = None
    default_speech_to_text_model: Optional[str] = None
    default_embedding_model: Optional[str] = None
    default_tools_model: Optional[str] = None

    # Ordered fallback chains (automatic failover) - the primary field above
    # stays the first entry; these are additional models tried in order when
    # the primary fails (e.g. free-tier rate limit). See
    # open_notebook.ai.provision.provision_langchain_model().
    chat_fallback_models: List[str] = []
    transformation_fallback_models: List[str] = []
    large_context_fallback_models: List[str] = []
    tools_fallback_models: List[str] = []
    embedding_fallback_models: List[str] = []
    text_to_speech_fallback_models: List[str] = []
    speech_to_text_fallback_models: List[str] = []


class ProviderAvailabilityResponse(BaseModel):
    available: List[str] = Field(..., description="List of available providers")
    unavailable: List[str] = Field(..., description="List of unavailable providers")
    supported_types: Dict[str, List[str]] = Field(
        ..., description="Provider to supported model types mapping"
    )


# Transformations API models
class TransformationCreate(BaseModel):
    name: str = Field(..., description="Transformation name")
    title: str = Field(..., description="Display title for the transformation")
    description: str = Field(
        ..., description="Description of what this transformation does"
    )
    prompt: str = Field(..., description="The transformation prompt")
    apply_default: bool = Field(
        False, description="Whether to apply this transformation by default"
    )
    model_id: Optional[str] = Field(
        None, description="Model ID to use by default for this transformation"
    )


class TransformationUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Transformation name")
    title: Optional[str] = Field(
        None, description="Display title for the transformation"
    )
    description: Optional[str] = Field(
        None, description="Description of what this transformation does"
    )
    prompt: Optional[str] = Field(None, description="The transformation prompt")
    apply_default: Optional[bool] = Field(
        None, description="Whether to apply this transformation by default"
    )
    model_id: Optional[str] = Field(
        None, description="Model ID to use by default for this transformation"
    )


class TransformationResponse(BaseModel):
    id: str
    name: str
    title: str
    description: str
    prompt: str
    apply_default: bool
    model_id: Optional[str] = None
    created: str
    updated: str


class TransformationExecuteRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(
        ..., description="ID of the transformation to execute"
    )
    input_text: str = Field(..., description="Text to transform")
    model_id: Optional[str] = Field(
        None, description="Model ID to use for this transformation run"
    )


class TransformationExecuteResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    output: str = Field(..., description="Transformed text")
    transformation_id: str = Field(..., description="ID of the transformation used")
    model_id: Optional[str] = Field(None, description="Model ID used")
    input_tokens: Optional[int] = Field(
        None, description="Actual input tokens reported by the provider for this run"
    )
    output_tokens: Optional[int] = Field(
        None, description="Actual output tokens reported by the provider for this run"
    )
    cost_status: Optional[Literal["known", "free", "unknown"]] = Field(
        None,
        description=(
            "Whether the real post-execution cost below is known, free, or "
            "unknown (no price available) - never a fake $0"
        ),
    )
    actual_cost_usd: Optional[float] = Field(
        None,
        description=(
            "Real cost recorded for this run from its usage_event, in USD. "
            "None when cost_status is not 'known'."
        ),
    )


class TransformationPreviewRequest(BaseModel):
    """Preview the exact prompt a transformation will send, before saving it."""

    prompt: str = Field(..., description="The transformation instructions to preview")
    sample_text: Optional[str] = Field(
        None,
        description=(
            "Optional sample source content to preview interpolation with. "
            "A generic placeholder is used when omitted."
        ),
    )


class TransformationPreviewResponse(BaseModel):
    system_prompt: str = Field(
        ..., description="The fully rendered system prompt that will be sent"
    )
    sample_input: str = Field(
        ...,
        description=(
            "The sample/placeholder content, sent as a separate user message "
            "(source content is never interpolated into the system prompt)"
        ),
    )


class TransformationCostEstimateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(..., description="ID of the transformation")
    input_text: str = Field(
        ..., description="The text that would be sent (used for the token estimate)"
    )
    model_id: Optional[str] = Field(
        None, description="Model override; falls back to the transformation/system default"
    )


class TransformationCostEstimateResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: Optional[str] = Field(None, description="Effective model that would be used")
    provider: Optional[str] = Field(None, description="Provider of the effective model")
    model_name: Optional[str] = Field(None, description="Name of the effective model")
    cost_status: Literal["known", "free", "unknown"] = Field(
        ..., description="Whether the estimate below is known, free, or unknown"
    )
    estimated_cost_usd: Optional[float] = Field(
        None, description="Best-effort estimated cost in USD; None when unknown"
    )
    estimated_input_tokens: int = Field(
        ..., description="Token estimate for the instructions + input text"
    )
    estimated_output_tokens: int = Field(
        ..., description="Rough output token estimate (actual usage is unknown ahead of time)"
    )
    is_estimate: bool = Field(
        True, description="Always true - real cost is only known after execution"
    )


class TransformationResultVersionResponse(BaseModel):
    version: int
    content: str
    created: Optional[str] = None
    is_latest: bool = False


class TransformationResultHistoryResponse(BaseModel):
    transformation_id: str
    source_id: str
    versions: List[TransformationResultVersionResponse]


# Default Prompt API models
class DefaultPromptResponse(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


class DefaultPromptUpdate(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


# Notes API models
class NoteCreate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: str = Field(..., description="Note content")
    note_type: Optional[str] = Field("human", description="Type of note (human, ai)")
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the note to"
    )


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: Optional[str] = Field(None, description="Note content")
    note_type: Optional[str] = Field(None, description="Type of note (human, ai)")


class NoteResponse(BaseModel):
    id: str
    title: Optional[str]
    content: Optional[str]
    note_type: Optional[str]
    created: str
    updated: str
    command_id: Optional[str] = None


# Embedding API models
class EmbedRequest(BaseModel):
    item_id: str = Field(..., description="ID of the item to embed")
    item_type: str = Field(..., description="Type of item (source, note)")
    async_processing: bool = Field(
        False, description="Process asynchronously in background"
    )


class EmbedResponse(BaseModel):
    success: bool = Field(..., description="Whether embedding was successful")
    message: str = Field(..., description="Result message")
    item_id: str = Field(..., description="ID of the item that was embedded")
    item_type: str = Field(..., description="Type of item that was embedded")
    command_id: Optional[str] = Field(
        None, description="Command ID for async processing"
    )


# Rebuild request/response models
class RebuildRequest(BaseModel):
    mode: Literal["existing", "all"] = Field(
        ...,
        description="Rebuild mode: 'existing' only re-embeds items with embeddings, 'all' embeds everything",
    )
    include_sources: bool = Field(True, description="Include sources in rebuild")
    include_notes: bool = Field(True, description="Include notes in rebuild")
    include_insights: bool = Field(True, description="Include insights in rebuild")


class RebuildResponse(BaseModel):
    command_id: str = Field(..., description="Command ID to track progress")
    total_items: int = Field(..., description="Estimated number of items to process")
    message: str = Field(..., description="Status message")


class RebuildProgress(BaseModel):
    processed: int = Field(..., description="Number of items processed")
    total: int = Field(..., description="Total items to process")
    percentage: float = Field(..., description="Progress percentage")


class RebuildStats(BaseModel):
    sources: int = Field(0, description="Sources processed")
    notes: int = Field(0, description="Notes processed")
    insights: int = Field(0, description="Insights processed")
    failed: int = Field(0, description="Failed items")


class RebuildStatusResponse(BaseModel):
    command_id: str = Field(..., description="Command ID")
    status: str = Field(..., description="Status: queued, running, completed, failed")
    progress: Optional[RebuildProgress] = None
    stats: Optional[RebuildStats] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


# Settings API models
class SettingsResponse(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    docling_ocr: Optional[bool] = None
    docling_formulas: Optional[bool] = None
    docling_vision: Optional[bool] = None
    youtube_preferred_languages: Optional[List[str]] = None


class SettingsUpdate(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    docling_ocr: Optional[bool] = None
    docling_formulas: Optional[bool] = None
    docling_vision: Optional[bool] = None
    youtube_preferred_languages: Optional[List[str]] = None


# Sources API models
class AssetModel(BaseModel):
    file_path: Optional[str] = None
    url: Optional[str] = None


class SourceCreate(BaseModel):
    # Backward compatibility: support old single notebook_id
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the source to (deprecated, use notebooks)"
    )
    # New multi-notebook support
    notebooks: Optional[List[str]] = Field(
        None,
        max_length=50,
        description="List of notebook IDs to add the source to (max 50)",
    )
    # Required fields
    type: str = Field(..., description="Source type: link, upload, or text")
    url: Optional[str] = Field(None, description="URL for link type")
    file_path: Optional[str] = Field(None, description="File path for upload type")
    content: Optional[str] = Field(None, description="Text content for text type")
    title: Optional[str] = Field(None, description="Source title")
    transformations: Optional[List[str]] = Field(
        default_factory=list,
        max_length=50,
        description="Transformation IDs to apply (max 50)",
    )
    embed: bool = Field(False, description="Whether to embed content for vector search")
    delete_source: bool = Field(
        False, description="Whether to delete uploaded file after processing"
    )
    # New async processing support
    async_processing: bool = Field(
        False, description="Whether to process source asynchronously"
    )
    # Duplicate-detection override: the duplicate pre-check (text/upload
    # sources only - see api/routers/sources.py) returns 409 on a
    # content_hash match unless this is set, letting the client resubmit
    # the exact same request after the user confirms "add anyway".
    force_duplicate: bool = Field(
        False, description="Create even if an identical source already exists"
    )

    @model_validator(mode="after")
    def validate_notebook_fields(self):
        # Ensure only one of notebook_id or notebooks is provided
        if self.notebook_id is not None and self.notebooks is not None:
            raise ValueError(
                "Cannot specify both 'notebook_id' and 'notebooks'. Use 'notebooks' for multi-notebook support."
            )

        # Convert single notebook_id to notebooks array for internal processing
        if self.notebook_id is not None:
            self.notebooks = [self.notebook_id]
            # Keep notebook_id for backward compatibility in response

        # Set empty array if no notebooks specified (allow sources without notebooks)
        if self.notebooks is None:
            self.notebooks = []

        return self


class SourceUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Source title")
    topics: Optional[List[str]] = Field(None, description="Source topics")


class SourceResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    full_text: Optional[str]
    embedded: bool
    embedded_chunks: int
    file_available: Optional[bool] = None
    created: str
    updated: str
    # New fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict] = None
    # Notebook associations
    notebooks: Optional[List[str]] = None
    # Archive flag (mirrors Notebook.archived) - see migration 30
    archived: bool = False
    # Set when another source in one of this source's notebooks shares the
    # same content_hash. A UX hint, not a hard block - see
    # open_notebook/graphs/source.py save_source().
    duplicate_of: Optional[str] = None
    duplicate_of_title: Optional[str] = None


class SourceListResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    embedded: bool  # Boolean flag indicating if source has embeddings
    embedded_chunks: int  # Number of embedded chunks
    insights_count: int
    created: str
    updated: str
    file_available: Optional[bool] = None
    # Status fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict[str, Any]] = None
    archived: bool = False
    duplicate_of: Optional[str] = None


class SourceDeletePreview(BaseModel):
    """Explicit delete-impact summary - see Source.get_delete_preview()."""

    source_id: str
    chunk_count: int = Field(..., description="Embedded chunks that will be removed")
    insight_count: int = Field(..., description="Insights that will be removed")
    chat_session_count: int = Field(
        ...,
        description=(
            "Source-scoped chat sessions that reference this source directly "
            "and will be removed with it. Does not count inline citations "
            "inside notebook-wide chat answers - those have no foreign key "
            "anywhere in the schema."
        ),
    )
    notebook_count: int = Field(
        ..., description="Notebooks this source is linked to (unlinked, not deleted)"
    )


class SourceUploadLimits(BaseModel):
    """Single source of truth for upload limits the UI should display.

    Mirrors the real enforced limit (api/middleware.py MaxBodySizeMiddleware)
    instead of a hardcoded guess that could drift from it. There is no fixed
    file-type allowlist to report: content-core content-sniffs uploads
    (50+ formats) rather than relying on a hand-maintained extension list -
    see tests/test_upload_type_mitigations.py for why that's intentional.
    """

    max_upload_size_bytes: int
    max_upload_size_mb: float


# Insights API models
class SourceInsightResponse(BaseModel):
    id: str
    source_id: str
    insight_type: str
    content: str
    # Optional: insights created before migration 19 have no timestamps,
    # and the API must return null for them (never the string "None").
    created: Optional[str] = None
    updated: Optional[str] = None


class InsightCreationResponse(BaseModel):
    """Response for async insight creation."""

    status: Literal["pending"] = "pending"
    message: str = "Insight generation started"
    source_id: str
    transformation_id: str
    command_id: Optional[str] = None


class SaveAsNoteRequest(BaseModel):
    notebook_id: Optional[str] = Field(None, description="Notebook ID to add note to")


class CreateSourceInsightRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(..., description="ID of transformation to apply")
    model_id: Optional[str] = Field(
        None, description="Model ID (uses default if not provided)"
    )


# Source status response
class SourceStatusResponse(BaseModel):
    status: Optional[str] = Field(None, description="Processing status")
    message: str = Field(..., description="Descriptive message about the status")
    processing_info: Optional[Dict[str, Any]] = Field(
        None, description="Detailed processing information"
    )
    command_id: Optional[str] = Field(None, description="Command ID if available")


# Error response
class ErrorResponse(BaseModel):
    error: str
    message: str


# API Key Configuration models
class SetApiKeyRequest(BaseModel):
    """Request to set an API key for a provider."""

    api_key: Optional[str] = Field(None, description="API key for the provider")
    base_url: Optional[str] = Field(
        None, description="Base URL for URL-based providers (Ollama, OpenAI-compatible)"
    )
    endpoint: Optional[str] = Field(None, description="Endpoint URL for Azure OpenAI")
    api_version: Optional[str] = Field(None, description="API version for Azure OpenAI")
    endpoint_llm: Optional[str] = Field(
        None, description="Service-specific endpoint for LLM (Azure)"
    )
    endpoint_embedding: Optional[str] = Field(
        None, description="Service-specific endpoint for embedding (Azure)"
    )
    endpoint_stt: Optional[str] = Field(
        None, description="Service-specific endpoint for STT (Azure)"
    )
    endpoint_tts: Optional[str] = Field(
        None, description="Service-specific endpoint for TTS (Azure)"
    )
    service_type: Optional[Literal["llm", "embedding", "stt", "tts"]] = Field(
        None,
        description="Service type for OpenAI-compatible providers (llm, embedding, stt, tts)",
    )
    # Vertex AI specific fields
    vertex_project: Optional[str] = Field(
        None, description="Google Cloud Project ID for Vertex AI"
    )
    vertex_location: Optional[str] = Field(
        None, description="Google Cloud Region for Vertex AI (e.g., us-central1)"
    )
    vertex_credentials_path: Optional[str] = Field(
        None, description="Path to Google Cloud service account JSON file"
    )

    @field_validator(
        "api_key",
        "base_url",
        "endpoint",
        "api_version",
        "endpoint_llm",
        "endpoint_embedding",
        "endpoint_stt",
        "endpoint_tts",
        "vertex_project",
        "vertex_location",
        "vertex_credentials_path",
        mode="before",
    )
    @classmethod
    def validate_not_empty_string(cls, v: Optional[str]) -> Optional[str]:
        """Reject empty strings - convert to None or raise error."""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                return None  # Treat empty/whitespace-only as None
            return stripped
        return v


class ApiKeyStatusResponse(BaseModel):
    """Response showing which providers are configured and their source."""

    configured: Dict[str, bool] = Field(
        ..., description="Map of provider name to whether it is configured"
    )
    source: Dict[str, Literal["database", "environment", "none"]] = Field(
        ...,
        description="Map of provider name to configuration source (database, environment, or none)",
    )
    encryption_configured: bool = Field(
        ...,
        description="Whether OPEN_NOTEBOOK_ENCRYPTION_KEY is set (required to store keys in database)",
    )


class TestConnectionResponse(BaseModel):
    """Response from testing a provider connection."""

    provider: str = Field(..., description="Provider name that was tested")
    success: bool = Field(..., description="Whether connection test succeeded")
    message: str = Field(..., description="Result message with details")


class MigrateFromEnvRequest(BaseModel):
    """Request to migrate API keys from environment variables to database."""

    force: bool = Field(
        False, description="Force overwrite existing database configurations"
    )


class MigrationResult(BaseModel):
    """Response from migrating API keys from environment to database."""

    message: str = Field(..., description="Summary message")
    migrated: List[str] = Field(
        default_factory=list, description="Providers successfully migrated"
    )
    skipped: List[str] = Field(
        default_factory=list, description="Providers skipped (already in DB)"
    )
    errors: List[str] = Field(
        default_factory=list, description="Migration errors by provider"
    )


# Notebook delete cascade models
# Credential models

# Kept in sync with the provider registry
# (open_notebook/ai/provider_registry.py PROVIDERS — the backend source of
# truth). A Literal can't be built at runtime, so this is the one remaining
# manual copy; tests/test_credential_provider_validation.py enforces the sync.
# The frontend consumes GET /api/providers at runtime and needs no edit.
SupportedProvider = Literal[
    "openai",
    "anthropic",
    "google",
    "groq",
    "mistral",
    "deepseek",
    "xai",
    "openrouter",
    "dashscope",
    "minimax",
    "novita",
    "ppq",
    "cohere",
    "voyage",
    "elevenlabs",
    "deepgram",
    "ollama",
    "omlx",
    "azure",
    "vertex",
    "openai_compatible",
    "anthropic_compatible",
]


class ProviderInfoResponse(BaseModel):
    """Provider metadata from the provider registry."""

    name: str = Field(..., description="Provider identifier (e.g. openai)")
    display_name: str = Field(..., description="Human-friendly provider name")
    modalities: List[str] = Field(
        ..., description="Default modalities supported by the provider"
    )
    docs_url: Optional[str] = Field(
        None, description="Where to get an API key / set the provider up"
    )
    env_configured: bool = Field(
        ..., description="Whether the provider is configured via environment variables"
    )


class CapabilitiesResponse(BaseModel):
    """Runtime availability of the opt-in heavy extraction engines.

    Reflects what is actually importable/reachable in this container — not merely
    what the OPEN_NOTEBOOK_ENABLE_* flags request — so the UI can gate engine
    options honestly (e.g. still show "unavailable" while a first-boot install
    is in progress). See docs/7-DEVELOPMENT/decisions/ADR-007-optin-runtimes.md.
    """

    docling_available: bool = Field(
        ...,
        description="Docling is installed: the docling document engine, OCR toggle and image sources work.",
    )
    crawl4ai_available: bool = Field(
        ...,
        description="Crawl4AI is usable: the local package is installed OR a remote server is configured.",
    )
    crawl4ai_remote_configured: bool = Field(
        ...,
        description="A remote Crawl4AI endpoint is configured via CRAWL4AI_API_URL (no local install needed).",
    )


def validate_url_key_provider_required_fields(
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> None:
    """Shared required-field rule for providers that need BOTH a base URL and an
    API key (currently anthropic_compatible).

    Called from both the create path (CreateCredentialRequest validator, which sees
    the full request payload) and the update path
    (credentials_service.ensure_provider_required_fields, which runs against the
    merged credential). Raises ValueError when a required field is missing.
    """
    if (provider or "").lower() == "anthropic_compatible":
        if not base_url or not str(base_url).strip():
            raise ValueError("Anthropic-compatible credentials require a base URL")
        if not api_key or not str(api_key).strip():
            raise ValueError("Anthropic-compatible credentials require an API key")


class CreateCredentialRequest(BaseModel):
    """Request to create a new credential."""

    name: str = Field(..., description="Credential name")
    provider: SupportedProvider = Field(
        ..., description="Provider name (openai, anthropic, etc.)"
    )
    modalities: List[str] = Field(
        default_factory=list,
        description="Supported modalities (language, embedding, text_to_speech, speech_to_text)",
    )
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL (Azure)")
    api_version: Optional[str] = Field(None, description="API version (Azure)")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID (Vertex)")
    location: Optional[str] = Field(None, description="Location (Vertex)")
    credentials_path: Optional[str] = Field(
        None, description="Credentials file path (Vertex)"
    )
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )

    @model_validator(mode="after")
    def _validate_provider_required_fields(self):
        validate_url_key_provider_required_fields(
            self.provider, self.base_url, self.api_key
        )
        return self


class UpdateCredentialRequest(BaseModel):
    """Request to update an existing credential."""

    name: Optional[str] = Field(None, description="Credential name")
    modalities: Optional[List[str]] = Field(None, description="Supported modalities")
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL")
    api_version: Optional[str] = Field(None, description="API version")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID")
    location: Optional[str] = Field(None, description="Location")
    credentials_path: Optional[str] = Field(None, description="Credentials path")
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )


class CredentialResponse(BaseModel):
    """Response for a credential (never includes api_key)."""

    id: str
    name: str
    provider: str
    modalities: List[str]
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    endpoint_llm: Optional[str] = None
    endpoint_embedding: Optional[str] = None
    endpoint_stt: Optional[str] = None
    endpoint_tts: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None
    num_ctx: Optional[int] = None
    has_api_key: bool = False
    created: str
    updated: str
    model_count: int = 0
    decryption_error: Optional[str] = None


class CredentialDeleteResponse(BaseModel):
    """Response for credential deletion."""

    message: str
    deleted_models: int = 0


class DiscoveredModelResponse(BaseModel):
    """A model discovered from a provider."""

    name: str
    provider: str
    model_type: Optional[str] = None
    description: Optional[str] = None


class DiscoverModelsResponse(BaseModel):
    """Response from model discovery."""

    credential_id: str
    provider: str
    discovered: List[DiscoveredModelResponse]


class RegisterModelData(BaseModel):
    """A model to register with user-specified type."""

    name: str
    provider: str
    model_type: str  # Required: user specifies the type


class RegisterModelsRequest(BaseModel):
    """Request to register discovered models."""

    models: List[RegisterModelData]


class RegisterModelsResponse(BaseModel):
    """Response from model registration."""

    created: int
    existing: int


class NotebookDeletePreview(BaseModel):
    notebook_id: str = Field(..., description="ID of the notebook")
    notebook_name: str = Field(..., description="Name of the notebook")
    note_count: int = Field(..., description="Number of notes that will be deleted")
    exclusive_source_count: int = Field(
        ..., description="Number of sources only in this notebook"
    )
    shared_source_count: int = Field(
        ..., description="Number of sources shared with other notebooks"
    )


class NotebookDeleteResponse(BaseModel):
    message: str = Field(..., description="Success message")
    deleted_notes: int = Field(..., description="Number of notes deleted")
    deleted_sources: int = Field(..., description="Number of exclusive sources deleted")
    unlinked_sources: int = Field(
        ..., description="Number of sources unlinked from notebook"
    )
    deleted_chat_sessions: int = Field(
        ..., description="Number of chat sessions deleted"
    )


# Usage tracking API models
class UsagePeriodInfo(BaseModel):
    preset: str = Field(
        ..., description="7d | 30d | 365d | all | custom (legacy month=30d, year=365d)"
    )
    from_: Optional[str] = Field(
        None, alias="from", description="ISO start of the range (None = beginning)"
    )
    to: Optional[str] = Field(None, description="ISO end of the range (None = now)")

    model_config = ConfigDict(populate_by_name=True)


class UsageTokenTotals(BaseModel):
    input: int = Field(..., description="Total input tokens (includes cache reads)")
    output: int = Field(..., description="Total output tokens")
    cached_input: int = Field(
        ..., description="Cache-read input tokens (only from providers that report it)"
    )
    cached_input_reported_events: int = Field(
        ..., description="Number of events whose provider reported cache reads"
    )
    embedding: Optional[int] = Field(
        None, description="Embedding tokens; None when embeddings are not tracked"
    )
    embeddings_tracked: bool = Field(
        ..., description="Whether embedding calls are recorded at all"
    )


class UsageCostTotals(BaseModel):
    known_usd: float = Field(
        ..., description="Sum of costs that are known (paid known prices + free=0)"
    )
    known_cost_events: int
    free_events: int
    unknown_cost_events: int = Field(
        ..., description="Events whose price is unknown (excluded from known_usd)"
    )
    unknown_cost_input_tokens: int
    unknown_cost_output_tokens: int
    is_complete: bool = Field(
        ..., description="True when no event in the selection has an unknown cost"
    )


class UsageBreakdownItem(BaseModel):
    key: str
    provider: Optional[str] = None
    event_count: int
    failed_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_known_usd: float
    known_cost_events: int
    free_events: int
    unknown_cost_events: int


class UsageFilterOptions(BaseModel):
    models: List[str] = Field(default_factory=list)
    providers: List[str] = Field(default_factory=list)
    task_types: List[str] = Field(default_factory=list)


class UsageSummaryResponse(BaseModel):
    """Aggregated LLM usage/cost (GET /api/usage/summary).

    Single source of truth: usage_event rows. Unknown prices are never
    counted as $0 - see `cost.unknown_cost_events`.
    """

    event_count: int = Field(..., description="Number of LLM calls in the selection")
    failed_count: int = Field(..., description="Calls that ended in an error")
    period: UsagePeriodInfo
    generated_at: str = Field(..., description="ISO timestamp when computed")
    tokens: UsageTokenTotals
    cost: UsageCostTotals
    budget_usd: float = Field(
        ..., description="Configured budget in USD (STUDY_BUDGET_USD, default 10.0)"
    )
    by_provider: List[UsageBreakdownItem] = Field(default_factory=list)
    by_model: List[UsageBreakdownItem] = Field(default_factory=list)
    by_task_type: List[UsageBreakdownItem] = Field(default_factory=list)
    filter_options: UsageFilterOptions
    applied_filters: Dict[str, Optional[str]] = Field(default_factory=dict)
    pricing_table_version: str = Field(
        ..., description="Version of the static price table in effect"
    )


# Budget API models (P2 "Modelos, costo y confianza")
class BudgetPeriodStatus(BaseModel):
    limit_usd: Optional[float] = Field(
        None, description="Configured limit for this period; None = no cap"
    )
    spent_usd: float = Field(..., description="Known-cost spend so far this period")
    remaining_usd: Optional[float] = Field(
        None, description="limit_usd - spent_usd; None when no limit is configured"
    )
    is_over: bool = Field(..., description="Whether spend already reached/passed the limit")


class BudgetStatusResponse(BaseModel):
    daily_budget_usd: Optional[float] = Field(
        None, description="User-configured daily limit (None = not configured)"
    )
    monthly_budget_usd_configured: Optional[float] = Field(
        None,
        description=(
            "User-configured monthly limit (None = falling back to the "
            "STUDY_BUDGET_USD environment variable)"
        ),
    )
    daily: BudgetPeriodStatus
    monthly: BudgetPeriodStatus


class BudgetSettingsUpdate(BaseModel):
    daily_budget_usd: Optional[float] = Field(
        None, description="New daily limit in USD; omit/null clears it (no daily cap)"
    )
    monthly_budget_usd: Optional[float] = Field(
        None,
        description=(
            "New monthly limit in USD; omit/null falls back to STUDY_BUDGET_USD"
        ),
    )


class BudgetCheckRequest(BaseModel):
    estimated_cost_usd: float = Field(
        ..., ge=0, description="Estimated cost of the operation about to run"
    )


class BudgetCheckResponse(BaseModel):
    estimated_cost_usd: float
    would_exceed_daily: bool = Field(
        ..., description="Whether spent_usd + estimated_cost_usd would pass the daily limit"
    )
    would_exceed_monthly: bool = Field(
        ..., description="Whether spent_usd + estimated_cost_usd would pass the monthly limit"
    )
    daily: BudgetPeriodStatus
    monthly: BudgetPeriodStatus


# Study Tools API models (flashcards + quiz generation)
class GenerateFlashcardsRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook to generate flashcards from")
    name: str = Field(..., description="Display name for the study set")
    item_count: int = Field(
        10, description="Target number of flashcards to generate", ge=1, le=50
    )
    model_id: Optional[str] = Field(
        None,
        description="Model ID to use (uses the default transformation model if not provided)",
    )


class GenerateQuizRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook to generate a quiz from")
    name: str = Field(..., description="Display name for the study set")
    item_count: int = Field(
        10, description="Target number of quiz questions to generate", ge=1, le=50
    )
    model_id: Optional[str] = Field(
        None,
        description="Model ID to use (uses the default transformation model if not provided)",
    )


class StudySetGenerationResponse(BaseModel):
    """Response for POST /study/flashcards and POST /study/quiz."""

    job_id: str
    status: str
    message: str
    notebook_id: str
    name: str


class StudySetResponse(BaseModel):
    id: str
    notebook: str
    kind: Literal["flashcards", "quiz"]
    name: str
    items: List[Dict[str, Any]]
    model_id: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    due_count: int = Field(
        0, description="Flashcards due for spaced-repetition review right now (0 for quiz sets)"
    )


class StudySetListResponse(BaseModel):
    id: str
    notebook: str
    kind: Literal["flashcards", "quiz"]
    name: str
    item_count: int
    model_id: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    due_count: int = Field(
        0, description="Flashcards due for spaced-repetition review right now (0 for quiz sets)"
    )


class ReviewFlashcardRequest(BaseModel):
    """Body for POST /study/{study_set_id}/items/{item_index}/review.

    Self-graded recall difficulty, same 4-way scale as well-known
    spaced-repetition tools - drives the next due date (see
    open_notebook/study/models.py::score_flashcard_review).
    """

    rating: Literal["again", "hard", "good", "easy"] = Field(
        ..., description="How well the card was recalled just now"
    )


class ReviewFlashcardResponse(BaseModel):
    study_set_id: str
    item_index: int
    item: Dict[str, Any] = Field(..., description="The flashcard item with updated SRS state")
    due_count: int = Field(..., description="Remaining flashcards due in this set after this review")


class GradeFlashcardRequest(BaseModel):
    """Body for POST /study/{study_set_id}/items/{item_index}/grade.

    Guided study mode ("Modo guiado con IA"): the student types a free-text
    answer instead of self-grading a flipped card, and the AI checks it.
    """

    answer: str = Field(..., description="The student's free-text answer to the flashcard's front")
    attempt: int = Field(
        1, description="Attempt number for this card in this sitting (starts at 1)", ge=1
    )


class GradeFlashcardResponse(BaseModel):
    correct: bool = Field(..., description="Whether the AI judged the answer as demonstrating understanding")
    feedback: str = Field(..., description="AI feedback on the student's answer, in the student's own language")
    rating: Literal["again", "hard", "good", "easy"] = Field(
        ..., description="Spaced-repetition rating recorded for this attempt"
    )
    attempt: int = Field(..., description="Attempt number this response corresponds to")
    revealed_answer: Optional[str] = Field(
        None,
        description="The reference answer, present only when force-revealed after the max attempts",
    )
    item: Dict[str, Any] = Field(..., description="The flashcard item with updated SRS state")
    due_count: int = Field(..., description="Remaining flashcards due in this set after this review")


class SuspendFlashcardRequest(BaseModel):
    """Body for POST /study/{study_set_id}/items/{item_index}/suspend.

    Pauses (or resumes) spaced-repetition scheduling for one card without
    deleting it - see open_notebook/study/models.py::StudySet.set_item_suspended.
    """

    suspended: bool = Field(
        ..., description="True to suspend (exclude from due/review), False to resume"
    )


class SuspendFlashcardResponse(BaseModel):
    study_set_id: str
    item_index: int
    item: Dict[str, Any] = Field(..., description="The flashcard item with updated suspended flag")
    due_count: int = Field(..., description="Remaining flashcards due in this set after this change")


class WeakSourceStat(BaseModel):
    """One entry in StudyMetricsResponse.weak_sources.

    "Topic" here means the source (by its generation-time `source_title`
    citation, see Flashcard.source_title) a card came from - there is no
    separate topic/tag taxonomy on study content to hang a richer concept
    on, so this is the honest, smallest version: which source's cards you
    are recalling the least.
    """

    source_title: str
    accuracy_pct: int = Field(..., description="Share of reviewed cards from this source rated good/easy")
    reviewed_count: int = Field(..., description="Number of reviewed cards from this source")


class StudyMetricsResponse(BaseModel):
    """Response for GET /study/metrics.

    Deliberately simple and honest: no invented score, streak, or XP -
    every field here is either a direct count or a plain percentage
    computed from recorded review outcomes.
    """

    due_today: int = Field(..., description="Flashcards due for review right now (excludes suspended cards)")
    accuracy_pct: Optional[int] = Field(
        None,
        description=(
            "Share of reviewed flashcards whose most recent rating was "
            "good/easy. Null if nothing has been reviewed yet - never shown "
            "as a fabricated 0%."
        ),
    )
    reviewed_cards: int = Field(..., description="Flashcards with at least one recorded review")
    total_cards: int = Field(..., description="Total flashcards across the scoped study sets")
    weak_sources: List[WeakSourceStat] = Field(
        default_factory=list,
        description="Up to 5 sources with the lowest recall accuracy (min 2 reviewed cards each)",
    )


# Google Drive integration API models
class DriveAuthUrlResponse(BaseModel):
    """Response for GET /drive/auth-url."""

    auth_url: str = Field(
        ..., description="Google OAuth consent URL to redirect the browser to"
    )


class DriveStatusResponse(BaseModel):
    """Response for GET /drive/status."""

    connected: bool = Field(
        ..., description="Whether a usable Drive connection exists (read from the DB)"
    )
    configured: bool = Field(
        True, description="Whether the GOOGLE_DRIVE_* OAuth env vars are set"
    )
    account_email: Optional[str] = Field(
        None, description="Connected Google account email, for display"
    )
    granted_scopes: List[str] = Field(
        default_factory=list, description="OAuth scopes Google granted"
    )
    connected_at: Optional[str] = Field(None, description="ISO datetime of connection")
    last_sync_at: Optional[str] = Field(
        None, description="ISO datetime of the last successful list/import"
    )
    needs_reconnect: bool = Field(
        False, description="Grant revoked/expired or Drive scope missing"
    )
    reconnect_reason: Optional[str] = Field(
        None, description="'revoked' | 'missing_scope' when needs_reconnect"
    )


class DriveDisconnectResponse(BaseModel):
    """Response for DELETE /drive/disconnect."""

    message: str


class DriveFileResponse(BaseModel):
    """A single Drive file entry (metadata only)."""

    id: str
    name: str
    mime_type: str
    modified_time: Optional[str] = None
    icon_link: Optional[str] = None


class DriveFileListResponse(BaseModel):
    """Response for GET /drive/files."""

    files: List[DriveFileResponse]
    next_page_token: Optional[str] = None


class DriveImportRequest(BaseModel):
    """Request body for POST /drive/import."""

    file_id: str = Field(..., description="Google Drive file ID to import")
    notebook_id: str = Field(
        ..., description="Notebook ID to add the imported source to"
    )


# =============================================================================
# Audit log / activity history (P2 "Seguridad")
# =============================================================================


class ActivityItemResponse(BaseModel):
    """A single audit log entry. `action` is a canonical code (e.g.
    "notebook_deleted") the frontend translates to a sentence - never shown
    raw. `detail` is short, non-sensitive context; never secret values or
    full content of what was acted on."""

    id: str
    action: str
    detail: Optional[str] = None
    created: str


class ActivityListResponse(BaseModel):
    """Response for GET /api/activity."""

    items: List[ActivityItemResponse]
    total: int
    limit: int
    offset: int


# =============================================================================
# Privacy center (P2 "Seguridad") - export / delete all data
# =============================================================================


class PrivacyExportResponse(BaseModel):
    """Response for GET /api/privacy/export.

    A JSON snapshot of this instance's content metadata - notebooks,
    sources, notes, chat sessions, study sets, podcast episodes, and
    credential/Drive-connection *metadata only* (never secret values).
    Large binary blobs (uploaded files, podcast audio) are NOT inlined -
    each source/episode entry carries its on-disk file_path/audio_file
    reference instead, documented in `note`.
    """

    generated_at: str
    note: str
    notebooks: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    notes: List[Dict[str, Any]]
    chat_sessions: List[Dict[str, Any]]
    study_sets: List[Dict[str, Any]]
    episodes: List[Dict[str, Any]]
    credentials: List[Dict[str, Any]]
    drive_connection: Optional[Dict[str, Any]] = None


class PrivacyDeleteRequest(BaseModel):
    """Request body for DELETE /api/privacy/data.

    `confirm` must exactly equal the literal phrase the frontend shows the
    user in the destructive-confirmation dialog - a lightweight
    double-confirmation that this isn't a misclick, on top of the UI's own
    confirm dialog.
    """

    confirm: str = Field(
        ..., description='Must be exactly "DELETE ALL MY DATA"'
    )


class PrivacyDeleteResponse(BaseModel):
    """Response for DELETE /api/privacy/data: counts of what was removed."""

    message: str
    deleted: Dict[str, int]
