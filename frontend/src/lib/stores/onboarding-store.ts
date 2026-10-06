import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Tracks whether the first-time onboarding flow (create a notebook, add a
 * source, ask a question - see components/onboarding/OnboardingFlow.tsx)
 * has been completed or explicitly skipped in this browser. Open Notebook
 * is single-user / per-browser (see VISION.md), so localStorage is enough -
 * no server round trip needed.
 */
interface OnboardingState {
  completed: boolean
  /** True once the user has opened the flow at least once, so the home
   * dashboard doesn't re-show the welcome banner mid-flow. */
  dismissed: boolean
  complete: () => void
  dismiss: () => void
  reset: () => void
}

export const useOnboardingStore = create<OnboardingState>()(
  persist(
    (set) => ({
      completed: false,
      dismissed: false,
      complete: () => set({ completed: true, dismissed: true }),
      dismiss: () => set({ dismissed: true }),
      reset: () => set({ completed: false, dismissed: false }),
    }),
    {
      name: 'onboarding-storage',
    }
  )
)
