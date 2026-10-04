/** Matches api/models.py Drive* schemas (api/routers/drive.py). */

export interface DriveAuthUrlResponse {
  auth_url: string
}

export type DriveReconnectReason = 'revoked' | 'missing_scope'

/** GET /drive/status - read from the DB on every call, never contains tokens. */
export interface DriveStatusResponse {
  connected: boolean
  /** false when GOOGLE_DRIVE_CLIENT_ID/SECRET/REDIRECT_URI are not set */
  configured: boolean
  account_email?: string | null
  /** OAuth scopes Google actually granted (space-split `scope`) */
  granted_scopes: string[]
  connected_at?: string | null
  last_sync_at?: string | null
  needs_reconnect: boolean
  reconnect_reason?: DriveReconnectReason | string | null
}

export interface DriveDisconnectResponse {
  message: string
}

export interface DriveFile {
  id: string
  name: string
  mime_type: string
  modified_time?: string | null
  icon_link?: string | null
}

export interface DriveFileListResponse {
  files: DriveFile[]
  next_page_token?: string | null
}

export interface DriveImportRequest {
  file_id: string
  notebook_id: string
}
