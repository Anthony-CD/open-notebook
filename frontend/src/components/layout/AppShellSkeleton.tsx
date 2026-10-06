import { cn } from '@/lib/utils'

/**
 * Text-free, AppShell-shaped placeholder shown while the session, runtime
 * config or a route is still loading. It deliberately contains no strings:
 * it renders before the language is known (server HTML / first paint), so
 * it can never flash the wrong language and never causes a hydration
 * mismatch. Its job is to make sure a reload never shows a blank page.
 */
export function AppShellSkeleton({
  withSidebar = true,
  className,
}: {
  withSidebar?: boolean
  className?: string
}) {
  return (
    <div
      className={cn('flex h-screen overflow-hidden bg-background', className)}
      aria-busy="true"
      data-testid="app-shell-skeleton"
    >
      {withSidebar && (
        <div className="hidden md:flex w-64 shrink-0 flex-col gap-3 border-r bg-sidebar p-4">
          <div className="h-7 w-32 rounded-md bg-muted animate-pulse" />
          <div className="mt-4 space-y-2">
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="h-8 rounded-md bg-muted/70 animate-pulse" />
            ))}
          </div>
        </div>
      )}
      <PageContentSkeleton />
    </div>
  )
}

/** Content-area-only placeholder (for use inside an already-rendered AppShell). */
export function PageContentSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn('flex-1 min-w-0 p-6 space-y-6', className)}
      aria-busy="true"
      data-testid="page-content-skeleton"
    >
      <div className="h-8 w-48 rounded-md bg-muted animate-pulse" />
      <div className="max-w-4xl space-y-4">
        <div className="h-28 rounded-lg bg-muted/70 animate-pulse" />
        <div className="h-40 rounded-lg bg-muted/70 animate-pulse" />
        <div className="h-24 rounded-lg bg-muted/70 animate-pulse" />
      </div>
    </div>
  )
}

/** Small card-shaped placeholder, e.g. a Suspense fallback for one settings card. */
export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn('h-32 rounded-lg border bg-muted/50 animate-pulse', className)}
      aria-busy="true"
      data-testid="card-skeleton"
    />
  )
}
