import Navbar from './components/Navbar'
import LoginForm from './components/LoginForm'
import RegisterForm from './components/RegisterForm'
import { AuthProvider, useAuth } from './context/AuthContext'
import { RouterProvider, useRouter } from './context/RouterContext'
import AccessDeniedPage from './pages/AccessDeniedPage'
import AdminDashboard from './pages/AdminDashboard'
import ConsumerDashboard from './pages/ConsumerDashboard'
import ProviderDashboard from './pages/ProviderDashboard'
import SystemStatusPage from './pages/SystemStatusPage'

function AppRoutes() {
  const { currentPath, navigate } = useRouter()
  const { user, isAuthenticated, isLoading } = useAuth()

  // Helper to determine the default dashboard for an authenticated user
  const getUserDashboardPath = (role) => {
    if (role === 'ADMIN') return '/admin'
    if (role === 'PROVIDER') return '/provider'
    return '/consumer'
  }

  // Initial session restoration state
  if (isLoading) {
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
  }

  // 1. Status / Diagnostics Page
  if (currentPath === '/' || currentPath === '/status') {
    return <SystemStatusPage />
  }

  // 2. Login Page
  if (currentPath === '/login') {
    if (isAuthenticated) {
      // Redirect authenticated users to their authorized dashboard
      navigate(getUserDashboardPath(user.role))
      return null
    }
    return <LoginForm />
  }

  // 3. Consumer Registration Page
  if (currentPath === '/register') {
    if (isAuthenticated) {
      navigate(getUserDashboardPath(user.role))
      return null
    }
    return <RegisterForm />
  }

  // 4. Role-Protected Dashboard: Consumer
  if (currentPath === '/consumer') {
    if (!isAuthenticated) {
      navigate('/login')
      return null
    }
    if (user.role !== 'CONSUMER') {
      return <AccessDeniedPage requiredRole="CONSUMER" />
    }
    return <ConsumerDashboard />
  }

  // 5. Role-Protected Dashboard: Provider
  if (currentPath === '/provider') {
    if (!isAuthenticated) {
      navigate('/login')
      return null
    }
    if (user.role !== 'PROVIDER') {
      return <AccessDeniedPage requiredRole="PROVIDER" />
    }
    return <ProviderDashboard />
  }

  // 6. Role-Protected Dashboard: Admin
  if (currentPath === '/admin') {
    if (!isAuthenticated) {
      navigate('/login')
      return null
    }
    if (user.role !== 'ADMIN') {
      return <AccessDeniedPage requiredRole="ADMIN" />
    }
    return <AdminDashboard />
  }

  // Fallback route: unknown path
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
          onClick={() => navigate(isAuthenticated ? getUserDashboardPath(user.role) : '/login')}
        >
          {isAuthenticated ? 'Go to My Dashboard' : 'Go to Sign In'}
        </button>
      </div>
    </div>
  )
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
