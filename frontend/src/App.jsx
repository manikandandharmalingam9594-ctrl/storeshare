import Navbar from './components/Navbar'
import LoginForm from './components/LoginForm'
import RegisterForm from './components/RegisterForm'
import { AuthProvider, useAuth } from './context/AuthContext'
import { Navigate, RouterProvider, useRouter } from './context/RouterContext'
import AccessDeniedPage from './pages/AccessDeniedPage'
import AdminDashboard from './pages/AdminDashboard'
import ConsumerDashboard from './pages/ConsumerDashboard'
import ProviderDashboard from './pages/ProviderDashboard'
import SystemStatusPage from './pages/SystemStatusPage'

import { getUserDashboardPath, resolveRoute, RouteAction } from './services/routing'

export function AppRoutes() {
  const { currentPath, navigate } = useRouter()
  const { user, isAuthenticated, isLoading } = useAuth()

  const decision = resolveRoute({
    currentPath,
    isAuthenticated,
    userRole: user?.role,
    isLoading,
  })

  switch (decision.type) {
    case RouteAction.LOADING:
      return (
        <div className="auth-card-container">
          <div className="glass-card" style={{ textAlign: 'center', padding: '40px' }}>
            <span className="spinner" style={{ width: '28px', height: '28px', borderWidth: '3px', marginBottom: '16px' }} />
            <h3 style={{ fontSize: '1.1rem', color: '#f8fafc' }}>Verifying Authentication Session...</h3>
            <p style={{ fontSize: '0.85rem', color: '#94a3b8', marginTop: '6px' }}>
              Validating Bearer credentials against backend node...
            </p>
          </div>
        </div>
      )

    case RouteAction.REDIRECT:
      return <Navigate to={decision.redirectTo} replace={decision.replace} />

    case RouteAction.RENDER_STATUS:
      return <SystemStatusPage />

    case RouteAction.RENDER_LOGIN:
      return <LoginForm />

    case RouteAction.RENDER_REGISTER:
      return <RegisterForm />

    case RouteAction.RENDER_CONSUMER:
      return <ConsumerDashboard />

    case RouteAction.RENDER_PROVIDER:
      return <ProviderDashboard />

    case RouteAction.RENDER_ADMIN:
      return <AdminDashboard />

    case RouteAction.RENDER_ACCESS_DENIED:
      return <AccessDeniedPage requiredRole={decision.requiredRole} />

    case RouteAction.RENDER_NOT_FOUND:
    default:
      return (
        <div className="auth-card-container">
          <div className="glass-card" style={{ textAlign: 'center', maxWidth: '480px' }}>
            <h3 style={{ fontSize: '1.25rem', marginBottom: '8px' }}>Page Not Found</h3>
            <p style={{ color: '#94a3b8', marginBottom: '20px' }}>
              The requested route <code>{currentPath}</code> does not exist on this node.
            </p>
            <button
              type="button"
              className="action-btn"
              onClick={() => navigate(isAuthenticated ? getUserDashboardPath(user?.role) : '/login')}
            >
              {isAuthenticated ? 'Go to My Dashboard' : 'Go to Sign In'}
            </button>
          </div>
        </div>
      )
  }
}

export default function App() {
  return (
    <RouterProvider>
      <AuthProvider>
        <div className="grid-overlay" />
        <div className="app-container">
          <Navbar />
          <main>
            <AppRoutes />
          </main>
        </div>
      </AuthProvider>
    </RouterProvider>
  )
}
