'use client'

import { useAuth } from '@/lib/hooks/use-auth'
import { useVersionCheck } from '@/lib/hooks/use-version-check'
import { useRouter } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { ModalProvider } from '@/components/providers/ModalProvider'
import { CreateDialogsProvider } from '@/lib/hooks/use-create-dialogs'
import { CommandPalette } from '@/components/common/CommandPalette'
import { AppShellSkeleton } from '@/components/layout/AppShellSkeleton'

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { isAuthenticated, isLoading } = useAuth()
  const router = useRouter()
  const [hasCheckedAuth, setHasCheckedAuth] = useState(false)

  // Check for version updates once per session
  useVersionCheck()

  useEffect(() => {
    // Mark that we've completed the initial auth check
    if (!isLoading) {
      setHasCheckedAuth(true)

      // Redirect to login if not authenticated
      if (!isAuthenticated) {
        // Store the current path to redirect back after login
        const currentPath = window.location.pathname + window.location.search
        sessionStorage.setItem('redirectAfterLogin', currentPath)
        router.push('/login')
      }
    }
  }, [isAuthenticated, isLoading, router])

  // While the session is being restored (or we're redirecting to /login),
  // render an AppShell-shaped skeleton rather than a spinner on white or
  // `null` - a hard reload must never look like a blank page.
  if (isLoading || !hasCheckedAuth || !isAuthenticated) {
    return <AppShellSkeleton />
  }

  // Page-level render errors are handled by app/(dashboard)/error.tsx
  // (translated, with a retry that calls reset()).
  return (
    <CreateDialogsProvider>
      {children}
      {/* ModalProvider reads ?modal= via useSearchParams(); keep it behind
          its own Suspense boundary so it can never force a CSR bailout of
          the whole route. */}
      <Suspense fallback={null}>
        <ModalProvider />
      </Suspense>
      <CommandPalette />
    </CreateDialogsProvider>
  )
}
