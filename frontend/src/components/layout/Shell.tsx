import { Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Sidebar } from './Sidebar'
import { api } from '@/api/client'

export function Shell() {
  const { data: version } = useQuery({
    queryKey: ['version'],
    queryFn: api.version,
    staleTime: 60_000,
  })

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 p-4 md:p-6 lg:p-8">
          <Outlet />
        </main>
        <footer className="border-t border-border px-6 py-3 text-xs text-text/60 flex justify-between">
          <span>Funnel Intel</span>
          {version && (
            <span>v {version.commit}</span>
          )}
        </footer>
      </div>
    </div>
  )
}
