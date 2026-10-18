import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { useRouter } from '../context/RouterContext'

export default function LoginForm() {
  const { login } = useAuth()
  const { navigate } = useRouter()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [validationError, setValidationError] = useState('')
  const [apiError, setApiError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setValidationError('')
    setApiError('')

    const trimmedEmail = email.trim()
    if (!trimmedEmail) {
      setValidationError('Please enter your email address.')
      return
    }
    if (!/\S+@\S+\.\S+/.test(trimmedEmail)) {
      setValidationError('Please enter a valid email address.')
      return
    }
    if (!password) {
      setValidationError('Please enter your password.')
      return
    }

    setIsSubmitting(true)

    try {
      const verifiedUser = await login(trimmedEmail, password)
      // Redirect to correct dashboard based on verified backend role
      if (verifiedUser.role === 'ADMIN') {
        navigate('/admin')
      } else if (verifiedUser.role === 'PROVIDER') {
        navigate('/provider')
      } else {
        navigate('/consumer')
      }
    } catch (err) {
      console.error('Login error:', err)
      setApiError(err.message || 'Authentication failed. Please check your credentials.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-card-container">
      <div className="glass-card auth-card">
        <div className="auth-card-header">
          <div className="card-icon-box large">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          </div>
          <h2 className="auth-title">Welcome to Store-Share</h2>
          <p className="auth-subtitle">
            Sign in to access your storage node, encrypted files, and network credentials.
          </p>
        </div>

        {/* Validation or API Error Banner */}
        {(validationError || apiError) && (
          <div className="error-banner" id="login-error-banner">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ flexShrink: 0, marginTop: 2 }}
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div>
              <div className="error-title">Authentication Failed</div>
              <div className="error-hint">{validationError || apiError}</div>
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <label className="form-label" htmlFor="login-email">
              Email Address
            </label>
            <input
              id="login-email"
              type="email"
              className="form-input"
              placeholder="e.g. user@storeshare.io"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              autoComplete="email"
              autoFocus
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="login-password">
              Password
            </label>
            <input
              id="login-password"
              type="password"
              className="form-input"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="current-password"
            />
          </div>

          <button
            id="login-submit-btn"
            type="submit"
            className="action-btn"
            disabled={isSubmitting}
            style={{ marginTop: '12px' }}
          >
            {isSubmitting ? (
              <>
                <span className="spinner" />
                <span>Verifying Credentials...</span>
              </>
            ) : (
              <>
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
                  <polyline points="10 17 15 12 10 7" />
                  <line x1="15" y1="12" x2="3" y2="12" />
                </svg>
                <span>Sign In to Network</span>
              </>
            )}
          </button>
        </form>

        <div className="auth-footer">
          <span>Need a consumer account? </span>
          <button
            type="button"
            className="text-link-btn"
            onClick={() => navigate('/register')}
          >
            Register Consumer
          </button>
        </div>
      </div>
    </div>
  )
}
