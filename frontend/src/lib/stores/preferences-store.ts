import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Local, per-browser display preferences - separate from the backend
 * `/settings` resource (content-processing engines, embedding defaults,
 * etc. - see use-settings.ts). These never leave localStorage, matching
 * theme-store/notebook-view-store.
 */
export type Density = 'comfortable' | 'compact'

interface PreferencesState {
  hasHydrated: boolean
  /** List/card spacing used across the app's own list/card components. */
  density: Density
  /**
   * Global fallback model id, used as the initial value only where a
   * feature has no per-feature "last used model" memory of its own (most
   * generation dialogs already default to the server-side DefaultModels
   * config via a "default" sentinel - this is a client-side nudge for the
   * few surfaces that don't have anything else, e.g. the onboarding "ask a
   * question" step). `null` means no preference.
   */
  defaultModel: string | null
  setDensity: (density: Density) => void
  setDefaultModel: (modelId: string | null) => void
  setHasHydrated: (value: boolean) => void
}

export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      hasHydrated: false,
      density: 'comfortable',
      defaultModel: null,
      setDensity: (density) => set({ density }),
      setDefaultModel: (modelId) => set({ defaultModel: modelId }),
      setHasHydrated: (value) => set({ hasHydrated: value }),
    }),
    {
      name: 'preferences-storage',
      partialize: (state) => ({ density: state.density, defaultModel: state.defaultModel }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      },
    }
  )
)

/** Density-aware className helper for list/card padding - comfortable is the
 * existing default spacing, compact tightens it visibly. */
export function densityClass(density: Density, comfortable: string, compact: string): string {
  return density === 'compact' ? compact : comfortable
}
