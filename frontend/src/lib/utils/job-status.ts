/**
 * Single source of truth for background-job statuses in the UI.
 *
 * The API normalizes every job status to a small public vocabulary
 * (see open_notebook/utils/job_status.py):
 *
 *   queued | processing | completed | failed | canceled | unknown
 *
 * Older API builds (and a few legacy endpoints) still send the raw
 * surreal_commands values (`new`, `running`) or aliases (`pending`,
 * `submitted`, `error`, `cancelled`), and before the backend fix a status
 * could even arrive as an enum repr like `CommandStatus.NEW`. Everything goes
 * through `normalizeJobStatus()` so components never compare raw strings and
 * never render a raw status or backend message.
 */

export type JobStatus =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'canceled'
  | 'unknown'

export const JOB_STATUSES: readonly JobStatus[] = [
  'queued',
  'processing',
  'completed',
  'failed',
  'canceled',
  'unknown',
] as const

const ALIASES: Record<string, JobStatus> = {
  new: 'queued',
  queued: 'queued',
  pending: 'queued',
  submitted: 'queued',
  running: 'processing',
  processing: 'processing',
  in_progress: 'processing',
  completed: 'completed',
  complete: 'completed',
  success: 'completed',
  succeeded: 'completed',
  done: 'completed',
  failed: 'failed',
  failure: 'failed',
  error: 'failed',
  canceled: 'canceled',
  cancelled: 'canceled',
  unknown: 'unknown',
}

/** Map any raw status value to the canonical `JobStatus`. Never throws. */
export function normalizeJobStatus(raw: unknown): JobStatus {
  if (typeof raw !== 'string') return 'unknown'
  let text = raw.trim().toLowerCase()
  if (!text) return 'unknown'
  // "CommandStatus.NEW" -> "new"
  const dot = text.lastIndexOf('.')
  if (dot >= 0) text = text.slice(dot + 1)
  return ALIASES[text] ?? 'unknown'
}

/** Queued or processing: the job may still change state (keep polling). */
export function isJobActive(raw: unknown): boolean {
  const status = normalizeJobStatus(raw)
  return status === 'queued' || status === 'processing'
}

export function isJobFailed(raw: unknown): boolean {
  return normalizeJobStatus(raw) === 'failed'
}

export function isJobCanceled(raw: unknown): boolean {
  return normalizeJobStatus(raw) === 'canceled'
}

export function isJobCompleted(raw: unknown): boolean {
  return normalizeJobStatus(raw) === 'completed'
}

/** completed / failed / canceled: nothing more will happen. */
export function isJobTerminal(raw: unknown): boolean {
  const status = normalizeJobStatus(raw)
  return status === 'completed' || status === 'failed' || status === 'canceled'
}

/** Only queued jobs can be canceled (surreal_commands can't interrupt a running job). */
export function isJobCancelable(raw: unknown): boolean {
  return normalizeJobStatus(raw) === 'queued'
}

/** i18n key of the human label for a status (`jobStatus.*` in every locale). */
export function jobStatusLabelKey(raw: unknown): `jobStatus.${JobStatus}` {
  return `jobStatus.${normalizeJobStatus(raw)}`
}

/**
 * Real progress percentage (0-100) if the backend actually reported one,
 * otherwise `null` - callers must then show an indeterminate spinner, never a
 * made-up percentage.
 */
export function getJobProgressPercent(progress: unknown): number | null {
  let value: unknown = progress
  if (progress && typeof progress === 'object') {
    const obj = progress as Record<string, unknown>
    value = obj.percentage ?? obj.percent ?? obj.progress
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return Math.min(100, Math.max(0, value))
}
