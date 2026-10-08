import { type ReactNode, useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuth } from '@/shared/store/auth'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export function Providers({ children }: { children: ReactNode }) {
  const hydrate = useAuth((s) => s.hydrate)
  useEffect(() => hydrate(), [hydrate])
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
