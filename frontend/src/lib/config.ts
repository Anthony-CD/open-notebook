/**
 * Runtime configuration for the frontend.
 * This allows the same Docker image to work in different environments.
 */

import { AppConfig, BackendConfigResponse } from '@/lib/types/config'
import { getAuthToken } from '@/lib/auth-token'

// Build timestamp for debugging - set at build time
const BUILD_TIME = new Date().toISOString()

// Upper bounds for the startup config fetches. Without them a hung request
// (e.g. the backend waiting on its GitHub version check) left ConnectionGuard
// "checking" forever; with them it falls through to the error overlay.
const RUNTIME_CONFIG_TIMEOUT_MS = 5_000
const BACKEND_CONFIG_TIMEOUT_MS = 20_000

function timeoutSignal(ms: number): AbortSignal | undefined {
  return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(ms)
    : undefined
}

let config: AppConfig | null = null
let configPromise: Promise<AppConfig> | null = null
// Auth token the cached config was fetched with. /api/config only reports the
// version to authenticated callers, so a config cached before login (e.g. by
// ConnectionGuard or the login page) has no version; refetch once a token
// shows up instead of keeping that versionless config for the whole session.
let configToken: string | null = null

function dropStaleVersionlessConfig(): void {
  if (!config || config.version) return
  const token = getAuthToken()
  if (token && token !== configToken) {
    config = null
    configPromise = null
  }
}

/**
 * Get the API URL to use for requests.
 *
 * Priority:
 * 1. Runtime config from API server (/api/config endpoint)
 * 2. Environment variable (NEXT_PUBLIC_API_URL)
 * 3. Default fallback (http://localhost:5055)
 */
export async function getApiUrl(): Promise<string> {
  // If we already have config, return it
  if (config) {
    return config.apiUrl
  }

  // If we're already fetching, wait for that
  if (configPromise) {
    const cfg = await configPromise
    return cfg.apiUrl
  }

  // Start fetching config
  configPromise = fetchConfig()
  const cfg = await configPromise
  return cfg.apiUrl
}

/**
 * Get the full configuration.
 */
export async function getConfig(): Promise<AppConfig> {
  dropStaleVersionlessConfig()
  if (config) {
    return config
  }

  if (configPromise) {
    return await configPromise
  }

  configPromise = fetchConfig()
  return await configPromise
}

/**
 * Fetch configuration from the API or use defaults.
 */
async function fetchConfig(): Promise<AppConfig> {
  const isDev = process.env.NODE_ENV === 'development'
  
  if (isDev) {
    console.log('🔧 [Config] Starting configuration detection...')
    console.log('🔧 [Config] Build time:', BUILD_TIME)
  }

  // STEP 1: Try to get runtime config from Next.js server-side endpoint
  // This allows API_URL to be set at runtime (not baked into build)
  // Note: Endpoint is at /config (not /api/config) to avoid reverse proxy conflicts
  let runtimeApiUrl: string | null = null
  try {
    if (isDev) console.log('🔧 [Config] Attempting to fetch runtime config from /config endpoint...')
    const runtimeResponse = await fetch('/config', {
      cache: 'no-store',
      signal: timeoutSignal(RUNTIME_CONFIG_TIMEOUT_MS),
    })
    if (runtimeResponse.ok) {
      const runtimeData = await runtimeResponse.json()
      runtimeApiUrl = runtimeData.apiUrl
      // Treat empty string as "not set" to allow fallback to env var or default
      if (runtimeApiUrl === '') {
        runtimeApiUrl = null
      }
      if (isDev) console.log('✅ [Config] Runtime API URL from server:', runtimeApiUrl)
    } else {
      if (isDev) console.log('⚠️ [Config] Runtime config endpoint returned status:', runtimeResponse.status)
    }
  } catch (error) {
    if (isDev) console.log('⚠️ [Config] Could not fetch runtime config:', error)
  }

  // STEP 2: Fallback to build-time environment variable
  const envApiUrl = process.env.NEXT_PUBLIC_API_URL
  if (isDev) console.log('🔧 [Config] NEXT_PUBLIC_API_URL from build:', envApiUrl || '(not set)')

  // STEP 3: Smart default - prefer relative path to use Next.js Rewrites
  // This avoids CORS issues and port mapping complexities by proxying through Next.js
  const defaultApiUrl = ''

  if (typeof window !== 'undefined' && isDev) {
      console.log('🔧 [Config] Using relative path (rewrites) as default')
  }

  // Priority: Runtime config > Build-time env var > Smart default
  // Note: runtimeApiUrl must be checked against null explicitly as empty string might be valid if intended (though we treat '' as null above)
  const baseUrl = runtimeApiUrl !== null && runtimeApiUrl !== undefined ? runtimeApiUrl : (envApiUrl || defaultApiUrl)
  if (isDev) {
    console.log('🔧 [Config] Final base URL to try:', baseUrl)
    console.log('🔧 [Config] Selection priority: runtime=' + (runtimeApiUrl ? '✅' : '❌') +
                ', build-time=' + (envApiUrl ? '✅' : '❌') +
                ', smart-default=' + (!runtimeApiUrl && !envApiUrl ? '✅' : '❌'))
  }

  try {
    if (isDev) console.log('🔧 [Config] Fetching backend config from:', `${baseUrl}/api/config`)
    // Try to fetch runtime config from backend API
    const token = getAuthToken()
    const response = await fetch(`${baseUrl}/api/config`, {
      cache: 'no-store',
      signal: timeoutSignal(BACKEND_CONFIG_TIMEOUT_MS),
      // The endpoint is public (dbStatus is needed pre-login) but only
      // returns version info when the request is authenticated.
      ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    })

    if (response.ok) {
      const data: BackendConfigResponse = await response.json()
      config = {
        apiUrl: baseUrl, // Use baseUrl from runtime-config (Python no longer returns this)
        // '' = version not available (unauthenticated, or the backend could
        // not resolve one); UI hides the field instead of showing "unknown".
        version: typeof data.version === 'string' ? data.version.trim() : '',
        buildTime: BUILD_TIME,
        latestVersion: data.latestVersion || null,
        hasUpdate: data.hasUpdate || false,
        dbStatus: data.dbStatus, // Can be undefined for old backends
      }
      configToken = token
      if (isDev) console.log('✅ [Config] Successfully loaded API config:', config)
      return config
    } else {
      // Don't log error here - ConnectionGuard will display it
      throw new Error(`API config endpoint returned status ${response.status}`)
    }
  } catch (error) {
    // Don't log error here - ConnectionGuard will display it with proper UI
    throw error
  }
}

/**
 * Reset the configuration cache (useful for testing).
 */
export function resetConfig(): void {
  config = null
  configPromise = null
  configToken = null
}
