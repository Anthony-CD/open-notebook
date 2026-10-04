import axios from 'axios'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import apiClient from '@/lib/api/client'
import { getApiUrl } from '@/lib/config'

interface AuthState {
  isAuthenticated: boolean
  token: string | null
  isLoading: boolean
  /**
   * i18n key (e.g. 'auth.invalidPassword') - never a display string. The UI
   * renders it with t(error, errorParams) so login errors follow the
   * selected language.
   */
  error: string | null
  errorParams: Record<string, string | number> | null
  lastAuthCheck: number | null
  isCheckingAuth: boolean
  hasHydrated: boolean
  authRequired: boolean | null
  setHasHydrated: (state: boolean) => void
  checkAuthRequired: () => Promise<boolean>
  login: (password: string) => Promise<boolean>
  logout: () => void
  checkAuth: () => Promise<boolean>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      isAuthenticated: false,
      token: null,
      isLoading: false,
      error: null,
      errorParams: null,
      lastAuthCheck: null,
      isCheckingAuth: false,
      hasHydrated: false,
      authRequired: null,

      setHasHydrated: (state: boolean) => {
        set({ hasHydrated: state })
      },

      checkAuthRequired: async () => {
        try {
          const response = await apiClient.get<{ auth_enabled?: boolean }>('/auth/status', {
            headers: { 'Cache-Control': 'no-store' },
          })

          const required = response.data.auth_enabled || false
          set({ authRequired: required })

          // If auth is not required, mark as authenticated
          if (!required) {
            set({ isAuthenticated: true, token: 'not-required' })
          }

          return required
        } catch (error) {
          console.error('Failed to check auth status:', error)

          // If it's a network error, set a more helpful error message
          if (axios.isAxiosError(error) && !error.response) {
            set({
              error: 'auth.connectErrorHint',
              errorParams: null,
              authRequired: null  // Don't assume auth is required if we can't connect
            })
          } else {
            // For other errors, default to requiring auth to be safe
            set({ authRequired: true })
          }

          // Re-throw the error so the UI can handle it
          throw error
        }
      },

      login: async (password: string) => {
        set({ isLoading: true, error: null, errorParams: null })
        try {
          const apiUrl = await getApiUrl()

          // Deliberately raw fetch (not apiClient): this probes a candidate
          // password, so the interceptors must not overwrite the Authorization
          // header with the stored token or hard-redirect on 401.
          const response = await fetch(`${apiUrl}/api/notebooks`, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${password}`,
              'Content-Type': 'application/json'
            }
          })
          
          if (response.ok) {
            set({ 
              isAuthenticated: true, 
              token: password, 
              isLoading: false,
              lastAuthCheck: Date.now(),
              error: null,
              errorParams: null
            })
            return true
          } else {
            let errorKey: string
            let errorParams: Record<string, string | number> | null = null
            if (response.status === 401) {
              errorKey = 'auth.invalidPassword'
            } else if (response.status === 403) {
              errorKey = 'auth.accessDenied'
            } else if (response.status >= 500) {
              errorKey = 'auth.serverError'
            } else {
              errorKey = 'auth.authFailedStatus'
              errorParams = { status: response.status }
            }

            set({
              error: errorKey,
              errorParams,
              isLoading: false,
              isAuthenticated: false,
              token: null
            })
            return false
          }
        } catch (error) {
          console.error('Network error during auth:', error)
          let errorKey: string
          let errorParams: Record<string, string | number> | null = null

          if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
            errorKey = 'auth.connectErrorHint'
          } else if (error instanceof Error) {
            errorKey = 'auth.networkError'
            errorParams = { message: error.message }
          } else {
            errorKey = 'auth.unexpectedError'
          }

          set({
            error: errorKey,
            errorParams,
            isLoading: false,
            isAuthenticated: false,
            token: null
          })
          return false
        }
      },
      
      logout: () => {
        set({
          isAuthenticated: false,
          token: null,
          error: null,
          errorParams: null
        })
      },
      
      checkAuth: async () => {
        const state = get()
        const { token, lastAuthCheck, isCheckingAuth, isAuthenticated } = state

        // If already checking, return current auth state
        if (isCheckingAuth) {
          return isAuthenticated
        }

        // If no token, not authenticated
        if (!token) {
          return false
        }

        // If we checked recently (within 30 seconds) and are authenticated, skip
        const now = Date.now()
        if (isAuthenticated && lastAuthCheck && (now - lastAuthCheck) < 30000) {
          return true
        }

        set({ isCheckingAuth: true })

        try {
          const apiUrl = await getApiUrl()

          // Deliberately raw fetch (not apiClient): a 401 here must update
          // store state, not trigger the interceptor's storage-clear/redirect.
          const response = await fetch(`${apiUrl}/api/notebooks`, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json'
            }
          })
          
          if (response.ok) {
            set({ 
              isAuthenticated: true, 
              lastAuthCheck: now,
              isCheckingAuth: false 
            })
            return true
          } else {
            set({
              isAuthenticated: false,
              token: null,
              lastAuthCheck: null,
              isCheckingAuth: false
            })
            return false
          }
        } catch (error) {
          console.error('checkAuth error:', error)
          set({ 
            isAuthenticated: false, 
            token: null,
            lastAuthCheck: null,
            isCheckingAuth: false 
          })
          return false
        }
      }
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.token,
        isAuthenticated: state.isAuthenticated
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      }
    }
  )
)