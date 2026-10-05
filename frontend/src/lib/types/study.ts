export type StudyKind = 'flashcards' | 'quiz'

export type StudyJobStatus =
  | 'running'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'error'
  | 'pending'
  | 'submitted'
  | 'unknown'

export const ACTIVE_STUDY_JOB_STATUSES: StudyJobStatus[] = [
  'running',
  'processing',
  'pending',
  'submitted',
]

export const FAILED_STUDY_JOB_STATUSES: StudyJobStatus[] = ['failed', 'error']

/** Self-graded recall difficulty, same 4-way scale as well-known
 * spaced-repetition tools (e.g. Anki). Drives the next due date - see
 * api/models.py::ReviewFlashcardRequest / open_notebook/study/models.py::score_flashcard_review. */
export type SrsRating = 'again' | 'hard' | 'good' | 'easy'

export interface FlashcardItem {
  front: string
  back: string
  /** Spaced-repetition state - absent until the card is reviewed for the
   * first time (a card with no `due` is treated as immediately due, same as
   * a "new" card in Anki). */
  due?: string | null
  interval?: number
  reps?: number
  ease?: number
  last_reviewed?: string | null
  /** Most recent rating recorded by score_flashcard_review - drives the
   * honest accuracy/weak-source metrics (see StudyMetrics below). */
  last_rating?: SrsRating | null
  /** True when the card's spaced-repetition schedule is paused - excluded
   * from due_count/next_due and the guided review queue until resumed. See
   * api/models.py::SuspendFlashcardRequest. */
  suspended?: boolean
  /** Lightweight citation: the source section this card was generated
   * from, if the generation prompt could identify one. */
  source_title?: string | null
}

export interface QuizItem {
  question: string
  options: string[]
  correct_index: number
  explanation?: string | null
  /** Lightweight citation: the source section this question was generated
   * from, if the generation prompt could identify one. */
  source_title?: string | null
}

/** Matches api/models.py StudySetResponse (GET /api/study/{id}). */
export interface StudySet {
  id: string
  notebook: string
  kind: StudyKind
  name: string
  items: FlashcardItem[] | QuizItem[]
  model_id?: string | null
  created?: string | null
  updated?: string | null
  job_status?: StudyJobStatus | null
  error_message?: string | null
  /** Flashcards due for spaced-repetition review right now (0 for quiz sets). */
  due_count?: number
}

/** Matches api/models.py StudySetListResponse (GET /api/notebooks/{id}/study). */
export interface StudySetListItem {
  id: string
  notebook: string
  kind: StudyKind
  name: string
  item_count: number
  model_id?: string | null
  created?: string | null
  updated?: string | null
  job_status?: StudyJobStatus | null
  error_message?: string | null
  /** Flashcards due for spaced-repetition review right now (0 for quiz sets). */
  due_count?: number
}

export interface GenerateStudySetRequest {
  notebook_id: string
  name: string
  item_count: number
  model_id?: string
}

/** Matches api/models.py StudySetGenerationResponse. */
export interface StudySetGenerationResponse {
  job_id: string
  status: string
  message: string
  notebook_id: string
  name: string
}

/** Mirrors PodcastJobStatus's shape - see StudyService.get_job_status. */
export interface StudyJobStatusResponse {
  job_id: string
  status: string
  result?: { study_set_id?: string; item_count?: number; error_message?: string } | null
  error_message?: string | null
  created?: string | null
  updated?: string | null
  progress?: unknown
}

/** Matches api/models.py ReviewFlashcardResponse (POST /study/{id}/items/{index}/review). */
export interface ReviewFlashcardResponse {
  study_set_id: string
  item_index: number
  item: FlashcardItem
  due_count: number
}

/** Body for POST /study/{id}/items/{index}/grade - guided study mode
 * ("Modo guiado con IA"). `attempt` starts at 1, the frontend increments it
 * on retry for the same card. */
export interface GradeFlashcardRequest {
  answer: string
  attempt: number
}

/** Matches api/models.py GradeFlashcardResponse (POST /study/{id}/items/{index}/grade). */
export interface GradeFlashcardResponse {
  correct: boolean
  feedback: string
  rating: SrsRating
  attempt: number
  /** The reference answer, present only when force-revealed after the max attempts. */
  revealed_answer?: string | null
  item: FlashcardItem
  due_count: number
}

/** Body for POST /study/{id}/items/{index}/suspend - pauses/resumes a
 * card's spaced-repetition schedule without deleting it. */
export interface SuspendFlashcardRequest {
  suspended: boolean
}

/** Matches api/models.py SuspendFlashcardResponse. */
export interface SuspendFlashcardResponse {
  study_set_id: string
  item_index: number
  item: FlashcardItem
  due_count: number
}

/** One entry in StudyMetrics.weak_sources - matches api/models.py
 * WeakSourceStat. "Topic" here means the source a card was generated from
 * (via its source_title citation) - there's no separate topic/tag
 * taxonomy to hang a richer concept on. */
export interface WeakSourceStat {
  source_title: string
  accuracy_pct: number
  reviewed_count: number
}

/** Matches api/models.py StudyMetricsResponse (GET /study/metrics). Every
 * field is a direct count or plain percentage from recorded review
 * outcomes - no invented score/streak/XP. */
export interface StudyMetrics {
  due_today: number
  /** Null if nothing has been reviewed yet - never a fabricated 0%. */
  accuracy_pct: number | null
  reviewed_cards: number
  total_cards: number
  weak_sources: WeakSourceStat[]
}

export function isStudyJobActive(status?: string | null): boolean {
  return !!status && (ACTIVE_STUDY_JOB_STATUSES as string[]).includes(status)
}

export function isFlashcardItem(item: FlashcardItem | QuizItem): item is FlashcardItem {
  return 'front' in item
}

export function isQuizItem(item: FlashcardItem | QuizItem): item is QuizItem {
  return 'question' in item
}
