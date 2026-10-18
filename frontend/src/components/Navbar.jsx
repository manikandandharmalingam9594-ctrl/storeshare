import { useAuth } from '../context/AuthContext'
import { useRouter } from '../context/RouterContext'

export default function Navbar() {
  const { user, isAuthenticated, logout } = useAuth()
  const { currentPath, navigate } = useRouter()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const getDashboardPath = () => {
    if (!user) return '/login'
    if (user.role === 'ADMIN') return '/admin'
    if (user.role === 'PROVIDER') return '/provider'
    return '/consumer'
  }

  return (
    <header className="top-nav">
      <div className="brand-wrapper" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        <div className="brand-icon">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
            <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
            <line x1="12" y1="22.08" x2="12" y2="12" />
          </svg>
        </div>
        <div>
          <h1 className="brand-name">Store-Share</h1>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        {/* Navigation links */}
        <button
          type="button"
          className={`nav-link-btn ${currentPath === '/' || currentPath === '/status' ? 'active' : ''}`}
          onClick={() => navigate('/status')}
        >
          System Health
        </button>

        {isAuthenticated && (
          <button
            type="button"
            className={`nav-link-btn ${currentPath === getDashboardPath() ? 'active' : ''}`}
            onClick={() => navigate(getDashboardPath())}
          >
            Dashboard
          </button>
        )}

        {/* User state and actions */}
        {isAuthenticated ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div className={`role-pill role-${user.role.toLowerCase()}`}>
              <span className="role-dot" />
              <span>{user.role}</span>
            </div>

            <span className="user-email-tag" title={user.email}>
              {user.email}
            </span>

            <button
              id="logout-btn"
              type="button"
              className="action-btn secondary small"
              onClick={handleLogout}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              <span>Logout</span>
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              id="nav-login-btn"
              type="button"
              className={`action-btn secondary small ${currentPath === '/login' ? 'active-outline' : ''}`}
              onClick={() => navigate('/login')}
            >
              Sign In
            </button>
            <button
              id="nav-register-btn"
              type="button"
              className={`action-btn small ${currentPath === '/register' ? 'active-outline' : ''}`}
              onClick={() => navigate('/register')}
            >
              Register
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
