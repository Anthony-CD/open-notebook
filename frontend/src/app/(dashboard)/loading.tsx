import { AppShellSkeleton } from '@/components/layout/AppShellSkeleton'

/**
 * Route-level loading UI for (dashboard) pages. Besides the navigation
 * fallback, it gives every page an implicit Suspense boundary, so a client
 * hook like useSearchParams() inside a page can never bail the whole route
 * out to an empty client-side render.
 */
export default function DashboardLoading() {
  return <AppShellSkeleton />
}
