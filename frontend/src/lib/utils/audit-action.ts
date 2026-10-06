/**
 * Single source of truth for mapping an audit log `action` code (see
 * api/audit_service.py ACTION_* constants) to a translated, human sentence.
 * Mirrors job-status.ts's normalize-then-translate pattern: components
 * never render a raw action code, and an unrecognized code (future backend
 * addition the frontend hasn't shipped a label for yet) falls back to a
 * generic "unknown action" sentence instead of breaking the page.
 */

export const KNOWN_AUDIT_ACTIONS = [
  'login_failed',
  'notebook_deleted',
  'source_deleted',
  'credential_created',
  'credential_deleted',
  'drive_connected',
  'drive_disconnected',
  'data_exported',
  'data_deleted',
] as const

export type AuditAction = (typeof KNOWN_AUDIT_ACTIONS)[number] | 'unknown'

export function normalizeAuditAction(raw: unknown): AuditAction {
  if (typeof raw !== 'string') return 'unknown'
  const text = raw.trim().toLowerCase()
  return (KNOWN_AUDIT_ACTIONS as readonly string[]).includes(text)
    ? (text as AuditAction)
    : 'unknown'
}

// Literal lookup (not a template string) so each full key - e.g.
// "activity.actions.login_failed" - appears verbatim in this file; the
// locale unused-key test greps source files for the literal key text,
// which a template string would never satisfy. Mirrors job-status.ts's
// JOB_STATUS_LABEL_KEYS fix for the same issue.
const AUDIT_ACTION_LABEL_KEYS: Record<AuditAction, string> = {
  login_failed: 'activity.actions.login_failed',
  notebook_deleted: 'activity.actions.notebook_deleted',
  source_deleted: 'activity.actions.source_deleted',
  credential_created: 'activity.actions.credential_created',
  credential_deleted: 'activity.actions.credential_deleted',
  drive_connected: 'activity.actions.drive_connected',
  drive_disconnected: 'activity.actions.drive_disconnected',
  data_exported: 'activity.actions.data_exported',
  data_deleted: 'activity.actions.data_deleted',
  unknown: 'activity.actions.unknown',
}

/** i18n key of the human label for an action (`activity.actions.*` in every locale). */
export function auditActionLabelKey(raw: unknown): string {
  return AUDIT_ACTION_LABEL_KEYS[normalizeAuditAction(raw)]
}
