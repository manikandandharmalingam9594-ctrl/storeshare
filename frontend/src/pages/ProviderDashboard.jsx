import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { authApi } from '../services/auth'

export default function ProviderDashboard() {
  const { user } = useAuth()

  // Storage node status state from coordinator
  const [nodeStatus, setNodeStatus] = useState(null)
  const [reconReport, setReconReport] = useState(null)
  const [jobTelemetry, setJobTelemetry] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Setup form state
  const [storageDir, setStorageDir] = useState('C:\\storeshare-storage')
  const [quotaGb, setQuotaGb] = useState('50')
  const [formSubmitting, setFormSubmitting] = useState(false)
  const [formSuccess, setFormSuccess] = useState(null)
  const [formError, setFormError] = useState(null)

  // Reconciliation filter & search state
  const [reconSearch, setReconSearch] = useState('')
  const [reconFilter, setReconFilter] = useState('ALL')

  // Copy command feedback
  const [copiedCmd, setCopiedCmd] = useState(false)

  // Fetch all telemetry data
  const fetchAllData = async (isInitial = false) => {
    if (isInitial) setLoading(true)
    else setRefreshing(true)

    try {
      const [status, recon, jobs] = await Promise.allSettled([
        authApi.getNodeStatus(),
        authApi.getProviderReconciliation(),
        authApi.getProviderJobHistory(25),
      ])

      if (status.status === 'fulfilled') {
        setNodeStatus(status.value)
        if (status.value.storage_dir && isInitial) {
          setStorageDir(status.value.storage_dir)
        }
        if (status.value.quota_gb && isInitial) {
          setQuotaGb(status.value.quota_gb.toString())
        }
      }

      if (recon.status === 'fulfilled') {
        setReconReport(recon.value)
      }

      if (jobs.status === 'fulfilled') {
        setJobTelemetry(jobs.value)
      }
    } catch (err) {
      console.error('Failed to fetch provider node telemetry:', err)
    } finally {
      if (isInitial) setLoading(false)
      setRefreshing(false)
    }
  }

  // Periodic polling every 5 seconds to update heartbeat, job queues, and chunk states
  useEffect(() => {
    fetchAllData(true)
    const interval = setInterval(() => {
      fetchAllData(false)
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  const executeNodeAction = async (action) => {
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
      const updated = await authApi.setupNodeStorage(trimmedDir, parsedQuota, action)
      setNodeStatus(updated)

      if (action === 'PAUSE') {
        setFormSuccess('Storage node status set to PAUSED. Existing stored chunks remain preserved; incoming STORE jobs will be paused.')
      } else if (action === 'STOP') {
        setFormSuccess('Storage node status set to STOPPED. Connected agent will halt job processing.')
      } else if (action === 'UPDATE') {
        setFormSuccess(`Quota updated safely to ${updated.quota_gb} GB without modifying or deleting existing stored chunks.`)
      } else {
        setFormSuccess(
          `Storage allocation configured (${updated.quota_gb} GB at ${updated.storage_dir}). ` +
          `Active agent will process jobs according to coordinator state.`
        )
      }

      // Refresh reconciliation and telemetry after action
      await fetchAllData(false)
    } catch (err) {
      setFormError(err.message || `Failed to perform action '${action}'.`)
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
          <span>Accepting Jobs (ONLINE)</span>
        </div>
      )
    }
    if (status === 'PAUSED') {
      return (
        <div className="status-badge paused" id="coordinator-status-badge">
          <span className="status-dot" />
          <span>New Storage Paused (PAUSED)</span>
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
  const agentCliCommand = `powershell -Command "$env:PYTHONPATH='.'; .venv\\Scripts\\python -m provider_agent --storage-dir \\"${storageDir || 'C:\\storeshare-storage'}\\" --quota-gb ${quotaGb || 50} --coordinator-url http://127.0.0.1:8001 --email ${user?.email || 'provider@example.com'} --password <your_password>"`

  const handleCopyCommand = () => {
    navigator.clipboard.writeText(agentCliCommand)
    setCopiedCmd(true)
    setTimeout(() => setCopiedCmd(false), 2500)
  }

  // Calculate quota usage metrics
  const quotaBytes = nodeStatus?.quota_bytes || 0
  const usedBytes = nodeStatus?.used_bytes || 0
  const freeDiskBytes = nodeStatus?.free_disk_bytes || 0
  const usedMb = (usedBytes / (1024 ** 2)).toFixed(2)
  const usedGb = (usedBytes / (1024 ** 3)).toFixed(3)
  const quotaGbVal = (quotaBytes / (1024 ** 3)).toFixed(2)
  const availableGb = (Math.max(0, quotaBytes - usedBytes) / (1024 ** 3)).toFixed(2)
  const freeDiskGb = freeDiskBytes > 0 ? (freeDiskBytes / (1024 ** 3)).toFixed(2) : 'Scanning...'
  const usedPercent = quotaBytes > 0 ? Math.min(100, Math.round((usedBytes / quotaBytes) * 100)) : 0

  // Filter reconciliation records
  const filteredReconRecords = (reconReport?.items || []).filter((item) => {
    if (reconFilter !== 'ALL' && item.status !== reconFilter) {
      return false
    }
    if (reconSearch.trim()) {
      const q = reconSearch.trim().toLowerCase()
      const matchCid = item.chunk_id.toLowerCase().includes(q)
      const matchFile = item.filename && item.filename.toLowerCase().includes(q)
      const matchDetail = item.status_detail && item.status_detail.toLowerCase().includes(q)
      return matchCid || matchFile || matchDetail
    }
    return true
  })

  return (
    <div className="dashboard-page">
      {/* Page Header */}
      <div className="dashboard-header-block">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="role-pill role-provider">
            <span className="role-dot" />
            <span>Provider Console</span>
          </div>
          <button
            type="button"
            className="action-btn secondary small"
            onClick={() => fetchAllData(false)}
            disabled={refreshing}
            style={{ width: 'auto', padding: '6px 14px' }}
          >
            {refreshing ? 'Refreshing...' : '↻ Refresh Status'}
          </button>
        </div>
        <h2 className="hero-title" style={{ marginTop: '12px', fontSize: '2.4rem' }}>
          Storage Node Provider Console
        </h2>
        <p className="hero-subtitle">
          Manage physical disk allocations, enforce storage quotas, and inspect real-time cryptographic reconciliation of stored encrypted chunks.
        </p>
      </div>

      {/* Warnings & Actionable Alerts */}
      {nodeStatus?.warnings && nodeStatus.warnings.length > 0 && (
        <div style={{ marginBottom: '20px' }}>
          {nodeStatus.warnings.map((warn, idx) => (
            <div key={idx} className="telemetry-warning-banner">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
              <div>
                <strong>Storage Alert:</strong> {warn}
              </div>
            </div>
          ))}
        </div>
      )}

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
              <span className="telemetry-label">Computer Hostname:</span>
              <span className="telemetry-value">{nodeStatus?.computer_name || 'Detecting...'}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Account Status:</span>
              <span className="telemetry-value success">
                {user?.is_active ? 'Active Node Operator' : 'Disabled'}
              </span>
            </div>
          </div>
        </div>

        {/* Node Capacity & Real Disk Allocation Card */}
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
              <h3 className="card-title">Storage Allocation & Physical Disk</h3>
            </div>
            {renderStatusBadge()}
          </div>

          <p className="card-desc">
            Real-time physical disk metrics, allocated StoreShare quota, and actual chunk usage reported by local agent.
          </p>

          <div className="quota-meter-container">
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem' }}>
              <span style={{ color: 'var(--text-secondary)' }}>StoreShare Quota Utilization:</span>
              <span style={{ fontWeight: 600, color: usedPercent >= 90 ? '#f43f5e' : (usedPercent >= 75 ? '#f59e0b' : '#10b981') }}>
                {usedPercent}% ({usedMb} MB / {quotaGbVal} GB)
              </span>
            </div>
            <div className="quota-meter-track">
              <div
                className="quota-meter-fill"
                style={{
                  width: `${usedPercent}%`,
                  backgroundColor: usedPercent >= 90 ? '#f43f5e' : (usedPercent >= 75 ? '#f59e0b' : '#10b981'),
                }}
              />
            </div>
          </div>

          <div className="telemetry-box">
            <div className="telemetry-row">
              <span className="telemetry-label">Configured Quota:</span>
              <span className="telemetry-value">{quotaGbVal} GB ({quotaBytes.toLocaleString()} bytes)</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Actual Used Space:</span>
              <span className="telemetry-value">{usedMb} MB ({usedBytes.toLocaleString()} bytes)</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Remaining Quota:</span>
              <span className="telemetry-value success">{availableGb} GB</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Physical Free Disk Space:</span>
              <span className="telemetry-value" style={{ color: freeDiskBytes > 0 && freeDiskBytes < (1024**3) ? '#f43f5e' : '#38bdf8' }}>
                {freeDiskGb} GB Free on Drive
              </span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Last Agent Heartbeat:</span>
              <span className="telemetry-value" style={{ fontSize: '0.8rem' }}>
                {nodeStatus?.last_heartbeat || 'None received yet'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Storage Setup & Safe Quota Management Card */}
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
            <h3 className="card-title">Storage Allocation & Operational Controls</h3>
          </div>
          {renderAgentState()}
        </div>

        <p className="card-desc">
          Safely modify storage directory, scale quota without data loss, or pause/stop accepting storage jobs.
        </p>

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
              <div className="error-title">Storage Operation Error</div>
              <div className="error-hint">{formError}</div>
            </div>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); executeNodeAction('START') }} noValidate>
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
                StoreShare usage ceiling in Gigabytes.
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '12px', marginTop: '12px', flexWrap: 'wrap' }}>
            <button
              id="provider-start-node-btn"
              type="submit"
              className="action-btn"
              disabled={formSubmitting || nodeStatus?.status === 'ONLINE'}
              style={{ flex: 1, minWidth: '180px' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <span>{nodeStatus?.status === 'PAUSED' ? 'Resume Accepting Jobs' : 'Start / Activate Node'}</span>
            </button>

            <button
              id="provider-pause-node-btn"
              type="button"
              className="action-btn warning"
              onClick={() => executeNodeAction('PAUSE')}
              disabled={formSubmitting || nodeStatus?.status === 'PAUSED' || nodeStatus?.status === 'STOPPED'}
              style={{ width: 'auto', padding: '0 20px' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
              <span>Pause Storage Jobs</span>
            </button>

            <button
              id="provider-update-quota-btn"
              type="button"
              className="action-btn secondary"
              onClick={() => executeNodeAction('UPDATE')}
              disabled={formSubmitting}
              style={{ width: 'auto', padding: '0 20px' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              <span>Update Quota Safely</span>
            </button>

            <button
              id="provider-stop-node-btn"
              type="button"
              className="action-btn danger"
              onClick={() => executeNodeAction('STOP')}
              disabled={formSubmitting || nodeStatus?.status === 'STOPPED'}
              style={{ width: 'auto', padding: '0 20px' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="6" y="6" width="12" height="12" />
              </svg>
              <span>Stop Storage Node</span>
            </button>
          </div>
        </form>
      </div>

      {/* Step 3: Physical Chunk Inventory & Cryptographic Reconciliation Card */}
      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
            </div>
            <div>
              <h3 className="card-title">Physical Chunk Inventory & Reconciliation</h3>
              <p className="card-desc" style={{ margin: 0 }}>
                Compares actual chunk files physically on provider disk against coordinator metadata across 5 integrity states.
              </p>
            </div>
          </div>
          <div className="status-badge idle">
            <span>Reconciled: {reconReport ? new Date(reconReport.reconciled_at).toLocaleTimeString() : 'Pending'}</span>
          </div>
        </div>

        {/* 5-State Summary Metrics */}
        <div className="recon-summary-grid">
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#38bdf8' }}>{reconReport?.total_expected ?? 0}</span>
            <span className="recon-stat-lbl">Expected Metadata</span>
          </div>
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#34d399' }}>{reconReport?.verified_count ?? 0}</span>
            <span className="recon-stat-lbl">Verified Physical</span>
          </div>
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#f87171' }}>{reconReport?.missing_count ?? 0}</span>
            <span className="recon-stat-lbl">Missing on Disk</span>
          </div>
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#fb7185' }}>{reconReport?.corrupted_count ?? 0}</span>
            <span className="recon-stat-lbl">Corrupted Checksum</span>
          </div>
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#fbbf24' }}>{reconReport?.unexpected_count ?? 0}</span>
            <span className="recon-stat-lbl">Unexpected / Orphan</span>
          </div>
          <div className="recon-stat-card">
            <span className="recon-stat-val" style={{ color: '#94a3b8' }}>{reconReport?.unverified_count ?? 0}</span>
            <span className="recon-stat-lbl">Unverified / Offline</span>
          </div>
        </div>

        {/* Search & Filter Toolbar */}
        <div style={{ display: 'flex', gap: '14px', marginBottom: '16px', flexWrap: 'wrap' }}>
          <input
            type="text"
            className="form-input"
            placeholder="Search chunk ID, filename, or details..."
            value={reconSearch}
            onChange={(e) => setReconSearch(e.target.value)}
            style={{ flex: 1, minWidth: '220px' }}
          />
          <select
            className="form-input"
            value={reconFilter}
            onChange={(e) => setReconFilter(e.target.value)}
            style={{ width: '200px' }}
          >
            <option value="ALL">All States ({reconReport?.items?.length ?? 0})</option>
            <option value="VERIFIED">Verified Only ({reconReport?.verified_count ?? 0})</option>
            <option value="MISSING">Missing Only ({reconReport?.missing_count ?? 0})</option>
            <option value="CORRUPTED">Corrupted Only ({reconReport?.corrupted_count ?? 0})</option>
            <option value="UNEXPECTED">Unexpected Only ({reconReport?.unexpected_count ?? 0})</option>
            <option value="UNVERIFIED">Unverified Only ({reconReport?.unverified_count ?? 0})</option>
          </select>
        </div>

        {/* Reconciliation Table */}
        <div style={{ overflowX: 'auto', background: 'rgba(5, 8, 15, 0.7)', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
          <table className="jobs-table">
            <thead>
              <tr>
                <th>Chunk ID</th>
                <th>File / Filename</th>
                <th>Expected Size</th>
                <th>Actual Disk Size</th>
                <th>Integrity Checksum (SHA-256)</th>
                <th>Physical State</th>
                <th>Audit Detail</th>
              </tr>
            </thead>
            <tbody>
              {filteredReconRecords.length === 0 ? (
                <tr>
                  <td colSpan="7" style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                    No chunk records found matching current filter.
                  </td>
                </tr>
              ) : (
                filteredReconRecords.map((item) => {
                  let badgeClass = 'badge-recon-unverified'
                  if (item.status === 'VERIFIED') badgeClass = 'badge-recon-verified'
                  else if (item.status === 'MISSING') badgeClass = 'badge-recon-missing'
                  else if (item.status === 'CORRUPTED') badgeClass = 'badge-recon-corrupted'
                  else if (item.status === 'UNEXPECTED') badgeClass = 'badge-recon-unexpected'

                  return (
                    <tr key={item.chunk_id}>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem', color: '#38bdf8' }}>
                        {item.chunk_id.length > 24 ? `${item.chunk_id.substring(0, 10)}...${item.chunk_id.substring(item.chunk_id.length - 8)}` : item.chunk_id}
                      </td>
                      <td>
                        {item.filename ? (
                          <div>
                            <span style={{ fontWeight: 600 }}>{item.filename}</span>
                            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block' }}>File #{item.file_id}</span>
                          </div>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>Unassigned</span>
                        )}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
                        {item.expected_size_bytes != null ? `${(item.expected_size_bytes / 1024).toFixed(1)} KB` : 'N/A'}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
                        {item.actual_size_bytes != null ? `${(item.actual_size_bytes / 1024).toFixed(1)} KB` : <span style={{ color: '#f87171' }}>Missing</span>}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }}>
                        {item.actual_checksum ? (
                          <span title={`SHA-256: ${item.actual_checksum}`}>
                            {item.actual_checksum.substring(0, 12)}...
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>None</span>
                        )}
                      </td>
                      <td>
                        <span className={`badge-recon ${badgeClass}`}>
                          {item.status}
                        </span>
                      </td>
                      <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                        {item.status_detail}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Step 4: Provider Health & Asynchronous Job Transfer Audit Card */}
      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </div>
            <div>
              <h3 className="card-title">Provider Health & Job Processing Audit</h3>
              <p className="card-desc" style={{ margin: 0 }}>
                Audit trail of asynchronous STORE_CHUNK, RETRIEVE_CHUNK, and DELETE_CHUNK operations handled by local agent.
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <span className="badge-recon badge-recon-verified">Active: {jobTelemetry?.active_jobs ?? 0}</span>
            <span className="badge-recon badge-recon-verified" style={{ background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8' }}>Completed: {jobTelemetry?.completed_jobs ?? 0}</span>
            <span className="badge-recon badge-recon-corrupted">Failed: {jobTelemetry?.failed_jobs ?? 0}</span>
          </div>
        </div>

        <div style={{ overflowX: 'auto', background: 'rgba(5, 8, 15, 0.7)', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
          <table className="jobs-table">
            <thead>
              <tr>
                <th>Job ID</th>
                <th>Type</th>
                <th>Chunk ID</th>
                <th>File ID</th>
                <th>Execution Status</th>
                <th>Error Message</th>
                <th>Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {!jobTelemetry?.recent_jobs || jobTelemetry.recent_jobs.length === 0 ? (
                <tr>
                  <td colSpan="7" style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                    No transfer jobs processed yet.
                  </td>
                </tr>
              ) : (
                jobTelemetry.recent_jobs.map((job) => (
                  <tr key={job.id}>
                    <td style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>#{job.id}</td>
                    <td>
                      <span style={{ fontWeight: 600, color: job.job_type === 'STORE_CHUNK' ? '#38bdf8' : (job.job_type === 'RETRIEVE_CHUNK' ? '#34d399' : '#f59e0b') }}>
                        {job.job_type}
                      </span>
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: '#94a3b8' }}>
                      {job.chunk_id.length > 20 ? `${job.chunk_id.substring(0, 10)}...${job.chunk_id.substring(job.chunk_id.length - 8)}` : job.chunk_id}
                    </td>
                    <td>File #{job.file_id}</td>
                    <td>
                      <span className={`badge-recon ${job.status === 'COMPLETED' ? 'badge-recon-verified' : (job.status === 'FAILED' ? 'badge-recon-corrupted' : 'badge-recon-unexpected')}`}>
                        {job.status}
                      </span>
                    </td>
                    <td style={{ fontSize: '0.78rem', color: job.error_message ? '#fb7185' : 'var(--text-muted)' }}>
                      {job.error_message || 'None (Successful)'}
                    </td>
                    <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                      {job.created_at ? new Date(job.created_at).toLocaleString() : 'N/A'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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
            <span>NAT Friendly: Outbound Only (TLS verify=True)</span>
          </div>
        </div>

        <p className="card-desc">
          Run the provider daemon on your computer using Python. It connects outward to the coordinator, validates filesystem space, enforces storage quota limits, rejects unsafe symlinks, and performs atomic fsync chunk operations.
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
          <span>🔒 <strong>Zero Plaintext:</strong> Operates strictly on client-side encrypted AES-256-GCM chunks.</span>
          <span>🛡️ <strong>Path Traversal Defense:</strong> Rejects symlinks and relative path escapes automatically.</span>
          <span>⚡ <strong>Atomic Fsync Writes:</strong> Flushes to staging file with os.fsync before atomic replacement.</span>
        </div>
      </div>
    </div>
  )
}
