import { lazy, Suspense } from 'react'
import { createHashRouter, RouterProvider, Outlet, Navigate } from 'react-router-dom'
import { AppShell } from '@/app/layout/AppShell'
import { useAuth } from '@/shared/store/auth'
import { HomePage } from '@/features/announcements/HomePage'
import { SearchPage } from '@/features/announcements/SearchPage'
import { HeatPage } from '@/features/heat/HeatPage'
import { LoginPage } from '@/features/auth/LoginPage'

const DetailPage = lazy(() =>
  import('@/features/announcements/DetailPage').then((m) => ({ default: m.DetailPage })),
)
const HeatDetailPage = lazy(() =>
  import('@/features/heat/HeatDetailPage').then((m) => ({ default: m.HeatDetailPage })),
)
const SubscriptionsPage = lazy(() =>
  import('@/features/subscriptions/SubscriptionsPage').then((m) => ({
    default: m.SubscriptionsPage,
  })),
)
const GraphPage = lazy(() =>
  import('@/features/graph/GraphPage').then((m) => ({ default: m.GraphPage })),
)
const SourcesPage = lazy(() =>
  import('@/features/ops/SourcesPage').then((m) => ({ default: m.SourcesPage })),
)

function SuspenseWrap() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <Outlet />
    </Suspense>
  )
}

function RequireAuth() {
  const isAuth = useAuth((s) => s.isAuthenticated)
  return isAuth ? <Outlet /> : <Navigate to="/login" replace />
}

const router = createHashRouter([
  {
    element: (
      <AppShell>
        <Outlet />
      </AppShell>
    ),
    children: [
      { path: '/', element: <HomePage /> },
      { path: '/search', element: <SearchPage /> },
      { path: '/heat', element: <HeatPage /> },
      { path: '/login', element: <LoginPage /> },
      {
        element: <SuspenseWrap />,
        children: [
          { path: '/announcements/:id', element: <DetailPage /> },
          { path: '/heat/:clusterId', element: <HeatDetailPage /> },
          { path: '/graph', element: <GraphPage /> },
          { path: '/ops/sources', element: <SourcesPage /> },
          {
            element: <RequireAuth />,
            children: [{ path: '/subscriptions', element: <SubscriptionsPage /> }],
          },
        ],
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])

export function Router() {
  return <RouterProvider router={router} />
}
