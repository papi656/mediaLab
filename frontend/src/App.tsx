import { Link, NavLink, Route, Routes } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { useLogout, useMe } from '@/lib/hooks'
import { cn } from '@/lib/utils'
import GeneratePage from '@/routes/Generate'
import HistoryPage from '@/routes/History'
import LoginPage from '@/routes/Login'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const me = useMe()

  if (me.isPending) {
    return <div className="p-10 text-sm text-muted-foreground">Loading…</div>
  }
  if (!me.data?.authenticated) {
    return <LoginPage />
  }
  return <>{children}</>
}

function Shell() {
  const logout = useLogout()
  const me = useMe()

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-6">
          <Link to="/" className="text-sm font-semibold tracking-tight">
            mediaLab
          </Link>
          <nav className="flex items-center gap-1 text-sm">
            <NavItem to="/">Generate</NavItem>
            <NavItem to="/history">History</NavItem>
          </nav>
          <div className="ml-auto">
            {me.data?.authenticated ? (
              <Button variant="ghost" size="sm" onClick={() => logout.mutate()}>
                Log out
              </Button>
            ) : null}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route
            path="/"
            element={
              <RequireAuth>
                <GeneratePage />
              </RequireAuth>
            }
          />
          <Route
            path="/history"
            element={
              <RequireAuth>
                <HistoryPage />
              </RequireAuth>
            }
          />
          <Route
            path="*"
            element={
              <RequireAuth>
                <GeneratePage />
              </RequireAuth>
            }
          />
        </Routes>
      </main>
    </div>
  )
}

function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-1.5 transition-colors hover:bg-muted',
          isActive && 'bg-muted font-medium',
        )
      }
    >
      {children}
    </NavLink>
  )
}

export default function App() {
  return <Shell />
}
