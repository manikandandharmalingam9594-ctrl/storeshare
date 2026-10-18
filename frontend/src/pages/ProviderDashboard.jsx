import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { authApi } from '../services/auth'

export default function ProviderDashboard() {
  const { user } = useAuth()

  // Storage node status state from coordinator
  const [nodeStatus, setNodeStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Setup form state
  const [storageDir, setStorageDir] = useState('C:\\storeshare-storage')
  const [quotaGb, setQuotaGb] = useState('50')
  const [formSubmitting, setFormSubmitting] = useState(false)
  const [formSuccess, setFormSuccess] = useState(null)
  const [formError, setFormError] = useState(null)

  // Copy command feedback
  const [copiedCmd, setCopiedCmd] = useState(false)

  // Fetch node status
  const fetchStatus = async (isInitial = false) => {
    if (isInitial) setLoading(true)
    else setRefreshing(true)

    try {
      const status = await authApi.getNodeStatus()
      setNodeStatus(status)
      if (status.storage_dir && isInitial) {
        setStorageDir(status.storage_dir)
      }
      if (status.quota_gb && isInitial) {
        setQuotaGb(status.quota_gb.toString())
      }
    } catch (err) {
      console.error('Failed to fetch provider node status:', err)
    } finally {
      if (isInitial) setLoading(false)
      setRefreshing(false)
    }
  }

  // Periodic polling every 6 seconds to update heartbeat and online status
  useEffect(() => {
    fetchStatus(true)
    const interval = setInterval(() => {
      fetchStatus(false)
    }, 6000)
    return () => clearInterval(interval)
  }, [])

  const handleStartNode = async (e) => {
    e.preventDefault()
    setFormSuccess(null)
    setFormError(null)

    const trimmedDir = storageDir.trim()
    const parsedQuota = parseFloat(quotaGb)

    if (!trimmedDir) {
      setFormError('Please specify a valid local storage directory path.')
      return
    }
    if (isNaN(parsedQuota) || parsedQuota <= 0) {
      setFormError('Please enter a valid storage quota greater than 0 GB.')
      return
    }

    setFormSubmitting(true)
    try {
      const updated = await authApi.setupNodeStorage(trimmedDir, parsedQuota, 'START')
      setNodeStatus(updated)
      setFormSuccess(
        `Storage allocation configured (${updated.quota_gb} GB at ${updated.storage_dir}). ` +
        `Start your local Python provider agent to begin serving storage.`
      )
    } catch (err) {
      setFormError(err.message || 'Failed to configure storage node.')
    } finally {
      setFormSubmitting(false)
    }
  }

  const handleStopNode = async () => {
    setFormSuccess(null)
    setFormError(null)
    setFormSubmitting(true)

    try {
      const trimmedDir = storageDir.trim() || nodeStatus?.storage_dir || 'C:\\storeshare-storage'
      const parsedQuota = parseFloat(quotaGb) || nodeStatus?.quota_gb || 10
      const updated = await authApi.setupNodeStorage(trimmedDir, parsedQuota, 'STOP')
      setNodeStatus(updated)
      setFormSuccess('Storage node status set to STOPPED. Connected local agents will pause operations.')
    } catch (err) {
      setFormError(err.message || 'Failed to stop storage node.')
    } finally {
      setFormSubmitting(false)
    }
  }

  // Format coordinator connection status badge
  const renderStatusBadge = () => {
    const status = nodeStatus?.status || 'UNREGISTERED'
    if (status === 'ONLINE') {
      return (
        <div className="status-badge healthy" id="coordinator-status-badge">
          <span className="status-dot" />
          <span>Coordinator Connected (ONLINE)</span>
        </div>
      )
    }
    if (status === 'STOPPED') {
      return (
        <div className="status-badge error" id="coordinator-status-badge">
          <span className="status-dot" />
          <span>Node Suspended (STOPPED)</span>
        </div>
      )
    }
    if (status === 'OFFLINE') {
      return (
        <div className="status-badge warning" id="coordinator-status-badge">
          <span className="status-dot" />
          <span>Heartbeat Timed Out (OFFLINE)</span>
        </div>
      )
    }
    return (
      <div className="status-badge idle" id="coordinator-status-badge">
        <span className="status-dot" />
        <span>Awaiting Agent Connection (UNREGISTERED)</span>
      </div>
    )
  }

  // Format agent heartbeat running state
  const renderAgentState = () => {
    if (!nodeStatus?.last_heartbeat) {
      return (
        <div className="status-badge idle">
          <span className="status-dot" />
          <span>Agent Inactive / Never Connected</span>
        </div>
      )
    }
    if (nodeStatus.is_online) {
      return (
        <div className="status-badge healthy">
          <span className="status-dot" />
          <span>Agent Running & Heartbeating</span>
        </div>
      )
    }
    return (
      <div className="status-badge warning">
        <span className="status-dot" />
        <span>Agent Idle / Heartbeat Expired</span>
      </div>
    )
  }

  // CLI command string for local provider agent
  const agentCliCommand = `python -m provider_agent --storage-dir "${storageDir || 'C:\\storeshare-storage'}" --quota-gb ${quotaGb || 50} --coordinator-url http://127.0.0.1:8001 --email ${user?.email || 'provider@example.com'} --password <your_password>`

  const handleCopyCommand = () => {
    navigator.clipboard.writeText(agentCliCommand)
    setCopiedCmd(true)
    setTimeout(() => setCopiedCmd(false), 2500)
  }

  return (
    <div className="dashboard-page">
      {/* Page Header */}
      <div className="dashboard-header-block">
        <div className="role-pill role-provider">
          <span className="role-dot" />
          <span>Provider Console</span>
        </div>
        <h2 className="hero-title" style={{ marginTop: '12px', fontSize: '2.4rem' }}>
          Storage Node Provider Console
        </h2>
        <p className="hero-subtitle">
          Manage your storage provider credentials, allocate dedicated disk quota, and monitor local agent telemetry.
        </p>
      </div>

      <div className="dashboard-grid">
        {/* Account Profile Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
                  <line x1="4" y1="22" x2="4" y2="15" />
                </svg>
              </div>
              <h3 className="card-title">Provider Account</h3>
            </div>
            <div className="status-badge healthy">
              <span className="status-dot" />
              <span>Admin Authorized</span>
            </div>
          </div>

          <p className="card-desc">
            Authorized storage node identity provisioned through the administrative governance protocol.
          </p>

          <div className="telemetry-box">
            <div className="telemetry-row">
              <span className="telemetry-label">Provider ID:</span>
              <span className="telemetry-value">#{user?.id}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Email:</span>
              <span className="telemetry-value">{user?.email}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Role:</span>
              <span className="telemetry-value success">{user?.role}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Account Status:</span>
              <span className="telemetry-value success">
                {user?.is_active ? 'Active Node Operator' : 'Disabled'}
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Registered:</span>
              <span className="telemetry-value" style={{ fontSize: '0.8rem' }}>
                {user?.created_at || 'Recently Provisioned'}
              </span>
            </div>
          </div>
        </div>

        {/* Node Capacity & Telemetry Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                  <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                  <line x1="6" y1="6" x2="6.01" y2="6" />
                  <line x1="6" y1="18" x2="6.01" y2="18" />
                </svg>
              </div>
              <h3 className="card-title">Storage Node Capacity</h3>
            </div>
            {renderStatusBadge()}
          </div>

          <p className="card-desc">
            Real-time telemetry and committed storage chunk allocation tracked by coordinator.
          </p>

          <div className="telemetry-box">
            <div className="telemetry-row">
              <span className="telemetry-label">Daemon Status:</span>
              <span className="telemetry-value" style={{ color: nodeStatus?.is_online ? '#10b981' : '#f59e0b' }}>
                {nodeStatus?.is_online ? 'Active & Receiving Telemetry' : (nodeStatus?.status || 'Awaiting Connection')}
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Pledged Capacity:</span>
              <span className="telemetry-value">
                {nodeStatus?.quota_gb ?? 0} GB Committed ({nodeStatus ? (nodeStatus.quota_bytes / (1024**3)).toFixed(2) : 0} GB)
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Used Storage:</span>
              <span className="telemetry-value">
                {nodeStatus ? (nodeStatus.used_bytes / (1024**2)).toFixed(2) : 0} MB
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Available Quota:</span>
              <span className="telemetry-value success">
                {nodeStatus ? (nodeStatus.available_bytes / (1024**3)).toFixed(2) : 0} GB Free
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Last Heartbeat:</span>
              <span className="telemetry-value" style={{ fontSize: '0.8rem' }}>
                {nodeStatus?.last_heartbeat || 'None received yet'}
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Proof-of-Storage:</span>
              <span className="telemetry-value">Standby</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Network Protocol:</span>
              <span className="telemetry-value">HTTP REST v0.2.0 (NAT Outbound)</span>
            </div>
          </div>
        </div>
      </div>

      {/* Storage Setup Form Card */}
      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
              </svg>
            </div>
            <h3 className="card-title">Storage Allocation Setup</h3>
          </div>
          {renderAgentState()}
        </div>

        <p className="card-desc">
          Configure the storage directory and maximum quota on your host machine.
        </p>

        {/* Clear explanations per Requirements */}
        <div className="policy-notice" style={{ marginBottom: '20px' }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <div>
            <strong>Architecture & Security Notice:</strong>
            <ul style={{ paddingLeft: '16px', marginTop: '6px', listStyleType: 'disc' }}>
              <li><strong>Quota Limits Usage:</strong> The storage quota limits the maximum bytes Store-Share chunk files may occupy; it does <em>not</em> format or partition your hard drive.</li>
              <li><strong>Browser Sandbox Boundary:</strong> Web browsers cannot inspect or write arbitrary local filesystem paths directly. The separate local Python provider-agent process runs on your computer, validates the directory, enforces free space limits, and executes atomic chunk operations.</li>
              <li><strong>Availability Condition:</strong> Local configuration alone does <em>not</em> make your node available to consumers. The provider-agent must be actively running and transmitting verified telemetry to the coordinator.</li>
            </ul>
          </div>
        </div>

        {formSuccess && (
          <div className="success-banner" id="storage-setup-success-banner">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>{formSuccess}</span>
          </div>
        )}

        {formError && (
          <div className="error-banner" id="storage-setup-error-banner">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div>
              <div className="error-title">Configuration Error</div>
              <div className="error-hint">{formError}</div>
            </div>
          </div>
        )}

        <form onSubmit={handleStartNode} noValidate>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 220px', gap: '20px' }}>
            <div className="form-group">
              <label className="form-label" htmlFor="provider-storage-dir-input">
                Local Storage Directory Path
              </label>
              <input
                id="provider-storage-dir-input"
                type="text"
                className="form-input"
                placeholder="e.g. C:\storeshare-storage or D:\StoreShare"
                value={storageDir}
                onChange={(e) => setStorageDir(e.target.value)}
                disabled={formSubmitting}
              />
              <span className="form-hint">
                The absolute directory path on your host where encrypted chunk files will be stored.
              </span>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="provider-quota-input">
                Quota Limit (GB)
              </label>
              <input
                id="provider-quota-input"
                type="number"
                min="1"
                step="1"
                className="form-input"
                placeholder="50"
                value={quotaGb}
                onChange={(e) => setQuotaGb(e.target.value)}
                disabled={formSubmitting}
              />
              <span className="form-hint">
                Store-Share usage ceiling in Gigabytes.
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '14px', marginTop: '8px' }}>
            <button
              id="provider-start-node-btn"
              type="submit"
              className="action-btn"
              disabled={formSubmitting}
              style={{ flex: 1 }}
            >
              {formSubmitting ? (
                <>
                  <span className="spinner" />
                  <span>Configuring Storage...</span>
                </>
              ) : (
                <>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                  <span>Start / Save Storage Node</span>
                </>
              )}
            </button>

            <button
              id="provider-stop-node-btn"
              type="button"
              className="action-btn danger"
              onClick={handleStopNode}
              disabled={formSubmitting || nodeStatus?.status === 'STOPPED'}
              style={{ width: 'auto', padding: '0 24px' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="6" y="6" width="12" height="12" />
              </svg>
              <span>Stop Storage Node</span>
            </button>
          </div>
        </form>
      </div>

      {/* Local Python Agent Launch Instructions */}
      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="4 17 10 11 4 5" />
                <line x1="12" y1="19" x2="20" y2="19" />
              </svg>
            </div>
            <h3 className="card-title">Run Local Python Provider Agent</h3>
          </div>
          <div className="status-badge idle">
            <span>NAT Friendly: Outbound Only</span>
          </div>
        </div>

        <p className="card-desc">
          Run the provider daemon on your computer using Python. It connects outward to the coordinator, validates filesystem space, isolates chunk storage, and transmits encrypted telemetry heartbeats.
        </p>

        <div style={{ background: '#05080f', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '10px', padding: '16px', position: 'relative' }}>
          <pre style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: '#38bdf8', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {agentCliCommand}
          </pre>
          <button
            type="button"
            onClick={handleCopyCommand}
            className="action-btn secondary small"
            style={{ position: 'absolute', top: '12px', right: '12px' }}
          >
            {copiedCmd ? 'Copied!' : 'Copy Command'}
          </button>
        </div>

        <div style={{ marginTop: '14px', fontSize: '0.82rem', color: 'var(--text-secondary)', display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
          <span>🔒 <strong>No Plaintext Files:</strong> Operates strictly on opaque encrypted chunk payloads.</span>
          <span>🛡️ <strong>Path Traversal Defense:</strong> Rejects symlinks and relative escapes automatically.</span>
          <span>⚡ <strong>Atomic Writes:</strong> Uses temp staging and atomic replacement with os.fsync.</span>
        </div>
      </div>
    </div>
  )
}
