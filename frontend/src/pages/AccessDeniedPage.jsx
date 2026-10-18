import { useAuth } from '../context/AuthContext'
import { useRouter } from '../context/RouterContext'

export default function AccessDeniedPage({ requiredRole }) {
  const { user } = useAuth()
  const { navigate } = useRouter()

  const getAuthorizedDashboard = () => {
    if (!user) return '/login'
    if (user.role === 'ADMIN') return '/admin'
    if (user.role === 'PROVIDER') return '/provider'
    return '/consumer'
  }

  return (
    <div className="auth-card-container">
      <div className="glass-card auth-card" style={{ maxWidth: '520px' }}>
        <div className="auth-card-header">
          <div className="card-icon-box large" style={{ background: 'rgba(244, 63, 94, 0.1)', borderColor: 'rgba(244, 63, 94, 0.3)', color: '#f43f5e' }}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
            </svg>
          </div>
          <h2 className="auth-title" style={{ color: '#fda4af' }}>
            Access Denied
          </h2>
          <p className="auth-subtitle">
            Insufficient privileges for the requested route.
          </p>
        </div>

        <div className="telemetry-box" style={{ margin: '16px 0 20px' }}>
          <div className="telemetry-row">
            <span className="telemetry-label">Your Current Role:</span>
            <span className="telemetry-value error">{user?.role || 'Unauthenticated'}</span>
          </div>
          <div className="telemetry-row">
            <span className="telemetry-label">Required Role:</span>
            <span className="telemetry-value" style={{ color: '#f59e0b' }}>
              {requiredRole || 'Different Role'}
            </span>
          </div>
          <div className="telemetry-row">
            <span className="telemetry-label">Status:</span>
            <span className="telemetry-value error">403 Forbidden</span>
          </div>
        </div>

        <div className="policy-notice" style={{ marginBottom: '24px' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          <span>
            <strong>Security Boundary Notice:</strong> Frontend route protection guides navigation, but all data access is cryptographically enforced on the FastAPI backend using JWT token validation and role dependencies.
          </span>
        </div>

        <button
          type="button"
          className="action-btn"
          onClick={() => navigate(getAuthorizedDashboard())}
        >
          Return to My Authorized Dashboard
        </button>
      </div>
    </div>
  )
}
