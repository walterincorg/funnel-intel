import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App'
import { Shell } from './components/layout/Shell'
import { Dashboard } from './pages/Dashboard'
import { Competitors } from './pages/Competitors'
import { CompetitorDetail } from './pages/CompetitorDetail'
import { Pricing } from './pages/Pricing'
import { Compare } from './pages/Compare'
import { ScanDetail } from './pages/ScanDetail'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Intel dashboard — old funnel-intel pages under /intel */}
          <Route path="/intel" element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="competitors" element={<Competitors />} />
            <Route path="competitors/:id" element={<CompetitorDetail />} />
            <Route path="pricing" element={<Pricing />} />
            <Route path="compare" element={<Compare />} />
            <Route path="scans/:id" element={<ScanDetail />} />
          </Route>

          {/* Platform (Walter) — everything else */}
          <Route path="/*" element={<App />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
