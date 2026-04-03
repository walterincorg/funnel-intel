import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Shell } from '@/components/layout/Shell'
import { Dashboard } from '@/pages/Dashboard'
import { Competitors } from '@/pages/Competitors'
import { CompetitorDetail } from '@/pages/CompetitorDetail'
import { ScanDetail } from '@/pages/ScanDetail'
import { Pricing } from '@/pages/Pricing'
import { Compare } from '@/pages/Compare'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/competitors" element={<Competitors />} />
            <Route path="/competitors/:id" element={<CompetitorDetail />} />
            <Route path="/scans/:id" element={<ScanDetail />} />
            <Route path="/pricing" element={<Pricing />} />
            <Route path="/compare" element={<Compare />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
