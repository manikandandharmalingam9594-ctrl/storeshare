import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { authApi } from '../services/auth'

export default function AdminDashboard() {
  const { user } = useAuth()

  // Provider provisioning state
  const [providerEmail, setProviderEmail] = useState('')
  const [providerPassword, setProviderPassword] = useState('')
  const [provisionLoading, setProvisionLoading] = useState(false)
  const [provisionSuccess, setProvisionSuccess] = useState(null)
  const [provisionError, setProvisionError] = useState(null)

  const handleProvisionProvider = async (e) => {
    e.preventDefault()
    setProvisionSuccess(null)
    setProvisionError(null)

    const trimmed = providerEmail.trim()
    if (!trimmed || !/\S+@\S+\.\S+/.test(trimmed)) {
      setProvisionError('Please enter a valid email for the new provider.')
      return
    }
    if (providerPassword.length < 8) {
      setProvisionError('Provider password must be at least 8 characters long.')
      return
    }

    setProvisionLoading(true)

    try {
      // Calls POST /api/admin/providers with admin JWT
      const created = await authApi.createProvider(trimmed, providerPassword)
      setProvisionSuccess(
        `Successfully provisioned provider account: ${created.email} (ID #${created.id}). They can now log in.`
      )
      setProviderEmail('')
      setProviderPassword('')
    } catch (err) {
      console.error('Provider provisioning failed:', err)
      setProvisionError(err.message || 'Failed to provision provider account.')
    } finally {
      setProvisionLoading(false)
    }
  }

  return (
    <div className="dashboard-page">
      {/* Page Header */}
      <div className="dashboard-header-block">
        <div className="role-pill role-admin">
          <span className="role-dot" />
          <span>Admin Console</span>
        </div>
        <h2 className="hero-title" style={{ marginTop: '12px', fontSize: '2.4rem' }}>
          System Administrator Console
        </h2>
        <p className="hero-subtitle">
          Manage system users, provision storage provider nodes, and oversee network governance.
        </p>
      </div>

      <div className="dashboard-grid">
        {/* Account Profile Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
              </div>
              <h3 className="card-title">Admin Identity</h3>
            </div>
            <div className="status-badge healthy">
              <span className="status-dot" />
              <span>Full Privileges</span>
            </div>
          </div>

          <p className="card-desc">
            Administrator credentials provisioned via the secure offline CLI bootstrap protocol.
          </p>

          <div className="telemetry-box">
            <div className="telemetry-row">
              <span className="telemetry-label">Admin ID:</span>
              <span className="telemetry-value">#{user?.id}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Email:</span>
              <span className="telemetry-value">{user?.email}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Role:</span>
              <span className="telemetry-value" style={{ color: '#f59e0b', fontWeight: 'bold' }}>
                {user?.role}
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Account Status:</span>
              <span className="telemetry-value success">
                {user?.is_active ? 'Active Administrator' : 'Disabled'}
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Registered:</span>
              <span className="telemetry-value" style={{ fontSize: '0.8rem' }}>
                {user?.created_at || 'Bootstrapped'}
              </span>
            </div>
          </div>
        </div>

        {/* Live Provider Provisioning Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="8.5" cy="7" r="4" />
                  <line x1="20" y1="8" x2="20" y2="14" />
                  <line x1="23" y1="11" x2="17" y2="11" />
                </svg>
              </div>
              <h3 className="card-title">Provision Storage Provider</h3>
            </div>
            <div className="status-badge loading">
              <span>Admin Protected</span>
            </div>
          </div>

          <p className="card-desc">
            Authorize a new storage provider account via <code>POST /api/admin/providers</code>.
          </p>

          {provisionSuccess && (
            <div className="success-banner" id="provision-success-banner">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <polyline points="20 6 9 17 4 12" />
              </svg>
              <span>{provisionSuccess}</span>
            </div>
          )}

          {provisionError && (
            <div className="error-banner" id="provision-error-banner">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <div>
                <div className="error-title">Provisioning Failed</div>
                <div className="error-hint">{provisionError}</div>
              </div>
            </div>
          )}

          <form onSubmit={handleProvisionProvider} noValidate>
            <div className="form-group">
              <label className="form-label" htmlFor="prov-email">
                Provider Email Address
              </label>
              <input
                id="prov-email"
                type="email"
                className="form-input"
                placeholder="e.g. node-operator@datacenter.net"
                value={providerEmail}
                onChange={(e) => setProviderEmail(e.target.value)}
                disabled={provisionLoading}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="prov-pwd">
                Initial Password
              </label>
              <input
                id="prov-pwd"
                type="password"
                className="form-input"
                placeholder="At least 8 characters"
                value={providerPassword}
                onChange={(e) => setProviderPassword(e.target.value)}
                disabled={provisionLoading}
              />
            </div>

            <button
              id="provision-provider-btn"
              type="submit"
              className="action-btn"
              disabled={provisionLoading}
              style={{ marginTop: '12px' }}
            >
              {provisionLoading ? (
                <>
                  <span className="spinner" />
                  <span>Provisioning Account...</span>
                </>
              ) : (
                <>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  <span>Authorize & Create Provider</span>
                </>
              )}
            </button>
          </form>
        </div>
      </div>

      {/* Placeholder: User & Fleet Management */}
      <div className="placeholder-banner-card">
        <div className="placeholder-icon-wrap">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </div>
        <div>
          <h4 className="placeholder-title">
            Network Governance & Fleet Management — Upcoming Phase
          </h4>
          <p className="placeholder-text">
            Future phases will feature full user account audit trails, node capacity staking controls, automated heartbeat inspections, and decentralized storage consensus parameters.
          </p>
          <div className="placeholder-badge-group">
            <span className="spec-badge">Governance: Admin Root</span>
            <span className="spec-badge">Provider Creation: Active & Verified</span>
            <span className="spec-badge">Fleet Audit: Scheduled</span>
          </div>
        </div>
      </div>
    </div>
  )
}
