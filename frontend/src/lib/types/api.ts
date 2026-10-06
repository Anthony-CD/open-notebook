export interface NotebookResponse {
  id: string
  name: string
  description: string
  archived: boolean
  created: string
  updated: string
  source_count: number
  note_count: number
}

export interface NoteResponse {
  id: string
  title: string | null
  content: string | null
  note_type: string | null
  created: string
  updated: string
}

export interface SourceListResponse {
  id: string
  title: string | null
  topics?: string[]                  // Make optional to match Python API
  asset: {
    file_path?: string
    url?: string
  } | null
  embedded: boolean
  embedded_chunks: number            // ADD: From Python API
  insights_count: number
  created: string
  updated: string
  file_available?: boolean
  // ADD: Async processing fields from Python API
  command_id?: string
  status?: string
  processing_info?: Record<string, unknown>
  // Archive flag (mirrors NotebookResponse.archived) - see migration 30
  archived?: boolean
  // Set when another source in one of this source's notebooks shares the
  // same content hash. A UX hint, not a hard block.
  duplicate_of?: string | null
}

export interface SourceDetailResponse extends SourceListResponse {
  full_text: string
  notebooks?: string[]  // List of notebook IDs this source is linked to
  duplicate_of_title?: string | null
}

export type SourceResponse = SourceDetailResponse

export interface SourceDeletePreview {
  source_id: string
  chunk_count: number
  insight_count: number
  chat_session_count: number
  notebook_count: number
}

export interface SourceUploadLimits {
  max_upload_size_bytes: number
  max_upload_size_mb: number
}

export interface DuplicateSourceDetail {
  message: string
  duplicate: true
  existing_source_id: string
  existing_source_title: string | null
  existing_source_created: string
}

export interface SourceStatusResponse {
  status?: string
  message: string
  processing_info?: Record<string, unknown>
  command_id?: string
}

export interface SettingsResponse {
  default_content_processing_engine_doc?: string
  default_content_processing_engine_url?: string
  default_embedding_option?: string
  auto_delete_files?: string
  docling_ocr?: boolean
  docling_formulas?: boolean
  docling_vision?: boolean
  youtube_preferred_languages?: string[]
}

export interface Capabilities {
  docling_available: boolean
  crawl4ai_available: boolean
  crawl4ai_remote_configured: boolean
}

export interface CreateNotebookRequest {
  name: string
  description?: string
}

export interface UpdateNotebookRequest {
  name?: string
  description?: string
  archived?: boolean
}

export interface NotebookDeletePreview {
  notebook_id: string
  notebook_name: string
  note_count: number
  exclusive_source_count: number
  shared_source_count: number
}

export interface NotebookDeleteResponse {
  message: string
  deleted_notes: number
  deleted_sources: number
  unlinked_sources: number
}

export interface CreateNoteRequest {
  title?: string
  content: string
  note_type?: string
  notebook_id?: string
}

export interface CreateSourceRequest {
  // Backward compatibility: support old single notebook_id
  notebook_id?: string
  // New multi-notebook support
  notebooks?: string[]
  // Required fields
  type: 'link' | 'upload' | 'text'
  url?: string
  file_path?: string
  content?: string
  title?: string
  transformations?: string[]
  embed?: boolean
  delete_source?: boolean
  // New async processing support
  async_processing?: boolean
  // Resubmit after a 409 duplicate warning once the user confirms "add anyway"
  force_duplicate?: boolean
}

export interface UpdateNoteRequest {
  title?: string
  content?: string
  note_type?: string
}

export interface UpdateSourceRequest {
  title?: string
  type?: 'link' | 'upload' | 'text'
  url?: string
  content?: string
}

export interface APIError {
  detail: string
}

// Source Chat Types
// Base session interface with common fields
export interface BaseChatSession {
  id: string
  title: string
  created: string
  updated: string
  message_count?: number
  model_override?: string | null
}

export interface SourceChatSession extends BaseChatSession {
  source_id: string
  model_override?: string
}

// A structured citation backing a `[n]` marker in a chat/ask answer. Built
// backend-side from what was actually retrieved (api.models.Citation) -
// never derived from the raw answer text, so the chip the frontend renders
// is never a guess. `location` is null when the source type doesn't carry
// anything more precise than "the whole document" (e.g. a full source or
// note - there's no page/chunk tracking for those today).
export interface Citation {
  number: number
  ref_type: 'source' | 'source_insight' | 'note'
  ref_id: string
  title: string
  location: string | null
  snippet: string
}

export interface SourceChatMessage {
  id: string
  type: 'human' | 'ai'
  content: string
  timestamp?: string
  citations?: Citation[] | null
  feedback?: 'up' | 'down' | null
}

export interface SourceChatContextIndicator {
  sources: string[]
  insights: string[]
  notes: string[]
}

export interface MessageFeedbackRequest {
  rating: 'up' | 'down' | null
}

export interface SourceChatSessionWithMessages extends SourceChatSession {
  messages: SourceChatMessage[]
  context_indicators?: SourceChatContextIndicator
}

export interface CreateSourceChatSessionRequest {
  source_id: string
  title?: string
  model_override?: string
}

export interface UpdateSourceChatSessionRequest {
  title?: string
  model_override?: string
}

export interface SendMessageRequest {
  message: string
  model_override?: string
}

export interface SourceChatStreamEvent {
  type: 'user_message' | 'ai_message' | 'context_indicators' | 'complete' | 'error'
  content?: string
  data?: unknown
  message?: string
  timestamp?: string
}

// Notebook Chat Types
export interface NotebookChatSession extends BaseChatSession {
  notebook_id: string
}

export interface NotebookChatMessage {
  id: string
  type: 'human' | 'ai'
  content: string
  timestamp?: string
  citations?: Citation[] | null
  feedback?: 'up' | 'down' | null
}

export interface NotebookChatSessionWithMessages extends NotebookChatSession {
  messages: NotebookChatMessage[]
}

export interface CreateNotebookChatSessionRequest {
  notebook_id: string
  title?: string
  model_override?: string
}

export interface UpdateNotebookChatSessionRequest {
  title?: string
  model_override?: string | null
}

export interface SendNotebookChatMessageRequest {
  session_id: string
  message: string
  context: {
    sources: Array<Record<string, unknown>>
    notes: Array<Record<string, unknown>>
  }
  model_override?: string
}

export interface BuildContextRequest {
  notebook_id: string
  context_config: {
    sources: Record<string, string>
    notes: Record<string, string>
  }
}

export interface BuildContextResponse {
  context: {
    sources: Array<Record<string, unknown>>
    notes: Array<Record<string, unknown>>
  }
  token_count: number
  char_count: number
}

export interface RecentlyViewedResponse {
  type: 'notebook' | 'source'
  id: string
  title: string
  last_viewed_at: string
}
