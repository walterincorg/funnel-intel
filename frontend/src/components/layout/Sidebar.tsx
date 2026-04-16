import { NavLink, useLocation } from 'react-router-dom'
import { LayoutDashboard, Users, ScanSearch, DollarSign, Megaphone, Globe, Menu, X, ChevronDown } from 'lucide-react'
import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'

interface NavGroup {
  label: string
  icon: typeof LayoutDashboard
  children: { to: string; icon: typeof LayoutDashboard; label: string }[]
}

const groups: NavGroup[] = [
  {
    label: 'Funnel / Pricing',
    icon: DollarSign,
    children: [
      { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
      { to: '/competitor-scans', icon: ScanSearch, label: 'Scans' },
      { to: '/pricing', icon: DollarSign, label: 'Pricing' },
    ],
  },
  {
    label: 'Ad & Domain Intel',
    icon: Globe,
    children: [
      { to: '/ad-intel', icon: Megaphone, label: 'Ad Intel' },
      { to: '/domain-intel', icon: Globe, label: 'Domain Intel' },
    ],
  },
]

const standaloneLinks = [
  { to: '/competitors/manage', icon: Users, label: 'Manage Competitors' },
]

function isGroupActive(group: NavGroup, pathname: string) {
  return group.children.some(c =>
    c.to === '/' ? pathname === '/' : pathname.startsWith(c.to)
  )
}

export function Sidebar() {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {}
    for (const g of groups) {
      init[g.label] = isGroupActive(g, location.pathname)
    }
    return init
  })

  useEffect(() => {
    setExpanded(prev => {
      const next = { ...prev }
      for (const g of groups) {
        if (isGroupActive(g, location.pathname)) {
          next[g.label] = true
        }
      }
      return next
    })
  }, [location.pathname])

  const toggle = (label: string) =>
    setExpanded(prev => ({ ...prev, [label]: !prev[label] }))

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
          'md:translate-x-0 md:static md:h-screen md:sticky',
          open ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="p-5 border-b border-border">
          <h1 className="text-lg font-semibold text-text-bright flex items-center gap-2">
            <ScanSearch size={22} className="text-accent" />
            Funnel Intel
          </h1>
        </div>

        <nav className="flex-1 p-3 space-y-2">
          {groups.map(group => {
            const active = isGroupActive(group, location.pathname)
            const isOpen = expanded[group.label]
            const Icon = group.icon

            return (
              <div key={group.label}>
                <button
                  onClick={() => toggle(group.label)}
                  className={cn(
                    'flex items-center justify-between w-full px-3 py-2.5 rounded-lg text-sm transition-colors',
                    active
                      ? 'text-accent font-medium'
                      : 'text-text hover:bg-bg-hover hover:text-text-bright'
                  )}
                >
                  <span className="flex items-center gap-3">
                    <Icon size={18} />
                    {group.label}
                  </span>
                  <ChevronDown
                    size={14}
                    className={cn(
                      'transition-transform duration-200',
                      isOpen && 'rotate-180'
                    )}
                  />
                </button>

                {isOpen && (
                  <div className="ml-4 mt-1 space-y-0.5 border-l border-border/50 pl-3">
                    {group.children.map(({ to, icon: ChildIcon, label }) => (
                      <NavLink
                        key={to}
                        to={to}
                        onClick={() => setOpen(false)}
                        className={({ isActive }) =>
                          cn(
                            'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                            isActive
                              ? 'bg-accent-dim text-accent font-medium'
                              : 'text-text hover:bg-bg-hover hover:text-text-bright'
                          )
                        }
                      >
                        <ChildIcon size={16} />
                        {label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {/* Standalone links */}
          {standaloneLinks.map(({ to, icon: Icon, label }) => (
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
