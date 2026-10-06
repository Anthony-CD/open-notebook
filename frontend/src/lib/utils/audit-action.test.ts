import { describe, it, expect } from 'vitest'

import {
  auditActionLabelKey,
  KNOWN_AUDIT_ACTIONS,
  normalizeAuditAction,
} from './audit-action'

describe('normalizeAuditAction', () => {
  it('passes through every known action code', () => {
    for (const action of KNOWN_AUDIT_ACTIONS) {
      expect(normalizeAuditAction(action)).toBe(action)
    }
  })

  it('is case/whitespace tolerant', () => {
    expect(normalizeAuditAction('  Notebook_Deleted  ')).toBe('notebook_deleted')
  })

  it('falls back to "unknown" for unrecognized or non-string input', () => {
    expect(normalizeAuditAction('something_future_backend_added')).toBe('unknown')
    expect(normalizeAuditAction(null)).toBe('unknown')
    expect(normalizeAuditAction(undefined)).toBe('unknown')
    expect(normalizeAuditAction(42)).toBe('unknown')
  })
})

describe('auditActionLabelKey', () => {
  it('returns a literal "activity.actions.<code>" key for every known action', () => {
    for (const action of KNOWN_AUDIT_ACTIONS) {
      expect(auditActionLabelKey(action)).toBe(`activity.actions.${action}`)
    }
  })

  it('returns the unknown-action key for unrecognized input', () => {
    expect(auditActionLabelKey('not_a_real_action')).toBe('activity.actions.unknown')
  })
})
