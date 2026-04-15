import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Users, ScanSearch, DollarSign, GitCompare, Megaphone, Globe, Menu, X, Rocket } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

const links = [
  { to: '/', icon: Rocket, label: 'Ship List' },
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/competitors', icon: Users, label: 'Competitors' },
  { to: '/pricing', icon: DollarSign, label: 'Pricing' },
  { to: '/compare', icon: GitCompare, label: 'Compare' },
  { to: '/ad-intel', icon: Megaphone, label: 'Ad Intel' },
  { to: '/domain-intel', icon: Globe, label: 'Domain Intel' },
]

export function Sidebar() {
  const [open, setOpen] = useState(false)

  return (
    <>
      {/* Mobile toggle */}
      <button
        onClick={() => setOpen(!open)}
        className="fixed top-4 left-4 z-50 p-2 rounded-lg bg-bg-card border border-border md:hidden"
      >
        {open ? <X size={20} /> : <Menu size={20} />}
      </button>

      {/* Overlay */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'fixed top-0 left-0 h-full w-60 bg-bg-card border-r border-border z-40 flex flex-col transition-transform duration-200',
          'md:translate-x-0 md:static',
          open ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="p-5 border-b border-border">
          <h1 className="text-lg font-semibold text-text-bright flex items-center gap-2">
            <ScanSearch size={22} className="text-accent" />
            Funnel Intel
          </h1>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {links.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors',
                  isActive
                    ? 'bg-accent-dim text-accent font-medium'
                    : 'text-text hover:bg-bg-hover hover:text-text-bright'
                )
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
    </>
  )
}
