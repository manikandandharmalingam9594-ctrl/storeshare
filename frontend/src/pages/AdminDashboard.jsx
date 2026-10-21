import { useState, useEffect, useMemo, useCallback } from 'react'
import { useAuth } from '../context/AuthContext'
import { authApi } from '../services/auth'

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '—'
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

function formatDateTime(ts) {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    if (isNaN(d.getTime())) return ts
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ts
  }
}

function renderStorageStatusBadge(status) {
  switch (status) {
    case 'VERIFIED_PHYSICAL':
      return (
        <span className="storage-pill verified" title="Physically verified on provider disk">
          <span className="pill-dot pulse" />
          <span>Verified Physical</span>
        </span>
      )
    case 'PROVIDER_OFFLINE':
      return (
        <span className="storage-pill offline" title="Provider node is offline; physical chunk presence unverified">
          <span className="pill-dot" />
          <span>Provider Offline (Unverified)</span>
        </span>
      )
    case 'PHYSICALLY_MISSING':
      return (
        <span className="storage-pill missing" title="Provider is online, but chunk is missing from local disk">
          <span className="pill-dot" />
          <span>Missing from Disk</span>
        </span>
      )
    case 'DATABASE_ASSIGNED':
      return (
        <span className="storage-pill assigned" title="Assigned in database; chunk upload in progress">
          <span className="pill-dot" />
          <span>Database Assigned</span>
        </span>
      )
    case 'STORAGE_FAILED':
      return (
        <span className="storage-pill failed" title="Storage transfer job failed">
          <span className="pill-dot" />
          <span>Storage Failed</span>
        </span>
      )
    case 'DELETED':
      return (
        <span className="storage-pill deleted" title="File or chunk has been deleted">
          <span className="pill-dot" />
          <span>Deleted</span>
        </span>
      )
    default:
      return (
        <span className="storage-pill assigned">
          <span className="pill-dot" />
          <span>{status || 'Unknown'}</span>
        </span>
      )
  }
}

export default function AdminDashboard() {
  const { user } = useAuth()

  // Provider provisioning state
  const [providerEmail, setProviderEmail] = useState('')
  const [providerPassword, setProviderPassword] = useState('')
  const [provisionLoading, setProvisionLoading] = useState(false)
  const [provisionSuccess, setProvisionSuccess] = useState(null)
  const [provisionError, setProvisionError] = useState(null)

  // Storage Tracking state
  const [storageFiles, setStorageFiles] = useState([])
  const [trackingLoading, setTrackingLoading] = useState(true)
  const [trackingError, setTrackingError] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [selectedFile, setSelectedFile] = useState(null)
  const [copiedChunkId, setCopiedChunkId] = useState(null)

  const fetchTrackingData = useCallback(async () => {
    setTrackingLoading(true)
    setTrackingError(null)
    try {
      const records = await authApi.getFileStorageTracking()
      setStorageFiles(records)
    } catch (err) {
      console.error('Failed to fetch file storage tracking:', err)
      setTrackingError(err.message || 'Failed to load storage tracking data.')
    } finally {
      setTrackingLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTrackingData()
  }, [fetchTrackingData])

  const handleCopyChunkId = (e, chunkId) => {
    e.stopPropagation()
    navigator.clipboard?.writeText(chunkId)
    setCopiedChunkId(chunkId)
    setTimeout(() => setCopiedChunkId(null), 2000)
  }

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
      const created = await authApi.createProvider(trimmed, providerPassword)
      setProvisionSuccess(
        `Successfully provisioned provider account: ${created.email} (ID #${created.id}). They can now log in.`
      )
      setProviderEmail('')
      setProviderPassword('')
      // Refresh tracking in case new node affects view
      fetchTrackingData()
    } catch (err) {
      console.error('Provider provisioning failed:', err)
      setProvisionError(err.message || 'Failed to provision provider account.')
    } finally {
      setProvisionLoading(false)
    }
  }

  // Filtered files based on search query and status filter
  const filteredFiles = useMemo(() => {
    return storageFiles.filter((f) => {
      if (statusFilter !== 'ALL' && f.storage_status !== statusFilter) {
        return false
      }

      if (!searchQuery.trim()) return true
      const q = searchQuery.toLowerCase().trim()
      const filenameMatch = f.filename?.toLowerCase().includes(q)
      const consumerMatch = f.consumer_email?.toLowerCase().includes(q) || String(f.consumer_id).includes(q)
      const fileIdMatch = String(f.file_id).includes(q)
      const providerMatch = f.provider_email?.toLowerCase().includes(q) || String(f.provider_node_id).includes(q)
      const computerMatch = f.computer_name?.toLowerCase().includes(q)
      const chunkMatch =
        f.chunk_id?.toLowerCase().includes(q) ||
        f.chunks?.some((c) => c.chunk_id?.toLowerCase().includes(q))

      return filenameMatch || consumerMatch || fileIdMatch || providerMatch || computerMatch || chunkMatch
    })
  }, [storageFiles, statusFilter, searchQuery])

  // Summary KPIs
  const kpiStats = useMemo(() => {
    let verifiedCount = 0
    let offlineCount = 0
    let missingCount = 0
    let deletedCount = 0

    storageFiles.forEach((f) => {
      if (f.storage_status === 'VERIFIED_PHYSICAL') verifiedCount++
      else if (f.storage_status === 'PROVIDER_OFFLINE') offlineCount++
      else if (f.storage_status === 'PHYSICALLY_MISSING') missingCount++
      else if (f.storage_status === 'DELETED') deletedCount++
    })

    return {
      total: storageFiles.length,
      verified: verifiedCount,
      offline: offlineCount,
      missing: missingCount,
      deleted: deletedCount,
    }
  }, [storageFiles])

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
          Trace physical provider nodes, verify disk chunk integrity, and provision storage providers.
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

      {/* =====================================================================
          Physical File Storage Tracking Section
          ===================================================================== */}
      <div className="tracking-section" id="storage-tracking-section">
        {/* Section Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: '12px' }}>
          <div>
            <div className="role-pill role-admin" style={{ marginBottom: '8px' }}>
              <span className="role-dot" />
              <span>Physical Storage Fleet</span>
            </div>
            <h3 style={{ fontSize: '1.6rem', fontWeight: 800, color: 'var(--text-primary)' }}>
              Physical File Storage Tracking
            </h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '4px' }}>
              Trace consumer encrypted files to provider nodes, physical host computer machines, and live chunk presence.
            </p>
          </div>
          <button
            id="refresh-tracking-btn"
            className="tracking-refresh-btn"
            onClick={fetchTrackingData}
            disabled={trackingLoading}
            title="Refresh live storage verification"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ animation: trackingLoading ? 'spin 1s linear infinite' : 'none' }}
            >
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
            <span>{trackingLoading ? 'Refreshing...' : 'Refresh Fleet'}</span>
          </button>
        </div>

        {/* KPI Summary Cards */}
        <div className="tracking-kpis">
          <div className="tracking-kpi-card" id="kpi-total-files">
            <span className="kpi-label">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              Total Files
            </span>
            <span className="kpi-value">{kpiStats.total}</span>
          </div>

          <div className="tracking-kpi-card" id="kpi-verified-chunks" style={{ borderColor: 'rgba(16, 185, 129, 0.25)' }}>
            <span className="kpi-label" style={{ color: '#34d399' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Physically Verified
            </span>
            <span className="kpi-value" style={{ color: '#34d399' }}>{kpiStats.verified}</span>
          </div>

          <div className="tracking-kpi-card" id="kpi-offline-nodes" style={{ borderColor: 'rgba(245, 158, 11, 0.25)' }}>
            <span className="kpi-label" style={{ color: '#fbbf24' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              Provider Offline (Unverified)
            </span>
            <span className="kpi-value" style={{ color: '#fbbf24' }}>{kpiStats.offline}</span>
          </div>

          <div className="tracking-kpi-card" id="kpi-missing-chunks" style={{ borderColor: 'rgba(239, 68, 68, 0.25)' }}>
            <span className="kpi-label" style={{ color: '#f87171' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
              Missing from Disk
            </span>
            <span className="kpi-value" style={{ color: '#f87171' }}>{kpiStats.missing}</span>
          </div>
        </div>

        {/* Toolbar: Search and Filter */}
        <div className="tracking-toolbar">
          <div className="tracking-search-wrap">
            <svg className="tracking-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              id="tracking-search-input"
              type="text"
              className="tracking-search-input"
              placeholder="Search consumer, filename, file ID, provider, computer..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <div className="tracking-filter-group">
            <label htmlFor="tracking-status-filter" style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
              Status Filter:
            </label>
            <select
              id="tracking-status-filter"
              className="tracking-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="ALL">All States ({storageFiles.length})</option>
              <option value="VERIFIED_PHYSICAL">Verified Physical</option>
              <option value="PROVIDER_OFFLINE">Provider Offline (Unverified)</option>
              <option value="PHYSICALLY_MISSING">Missing from Disk</option>
              <option value="DATABASE_ASSIGNED">Database Assigned</option>
              <option value="DELETED">Deleted Files</option>
              <option value="STORAGE_FAILED">Failed Storage</option>
            </select>
          </div>
        </div>

        {/* Storage Tracking Main Table Card */}
        <div className="tracking-table-card">
          {trackingError ? (
            <div className="error-banner" style={{ margin: '20px' }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <span>{trackingError}</span>
            </div>
          ) : trackingLoading && storageFiles.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
              <span className="spinner" style={{ width: '28px', height: '28px', margin: '0 auto 16px' }} />
              <div>Loading physical file storage telemetry...</div>
            </div>
          ) : filteredFiles.length === 0 ? (
            <div className="vault-empty-state">
              <svg className="vault-empty-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                <line x1="6" y1="6" x2="6.01" y2="6" />
                <line x1="6" y1="18" x2="6.01" y2="18" />
              </svg>
              <div className="vault-empty-title">No matching files found</div>
              <p className="vault-empty-desc">
                {searchQuery || statusFilter !== 'ALL'
                  ? 'Try clearing your search query or switching your status filter.'
                  : 'No files have been uploaded to StoreShare yet.'}
              </p>
            </div>
          ) : (
            <div className="tracking-table-scroll">
              <table className="tracking-table" id="storage-tracking-table">
                <thead>
                  <tr>
                    <th>Consumer</th>
                    <th>Filename & File ID</th>
                    <th>Provider Node & Identity</th>
                    <th>Computer Name</th>
                    <th>Chunk ID</th>
                    <th>Encrypted Size</th>
                    <th>Storage Status</th>
                    <th>Provider Connectivity</th>
                    <th>Last Verified</th>
                    <th>Latest Job Status</th>
                    <th style={{ textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredFiles.map((file) => {
                    const hasMultipleChunks = file.chunks && file.chunks.length > 1
                    return (
                      <tr
                        key={file.file_id}
                        className="clickable-row"
                        onClick={() => setSelectedFile(file)}
                        id={`file-row-${file.file_id}`}
                      >
                        {/* Consumer */}
                        <td>
                          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                            {file.consumer_email || `User #${file.consumer_id}`}
                          </div>
                          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                            Consumer #{file.consumer_id}
                          </div>
                        </td>

                        {/* Filename & ID */}
                        <td>
                          <div style={{ fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-all' }}>
                            {file.filename}
                          </div>
                          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                            File ID #{file.file_id}
                            {hasMultipleChunks && (
                              <span style={{ marginLeft: '6px', color: 'var(--accent-cyan)' }}>
                                ({file.chunks.length} chunks)
                              </span>
                            )}
                          </div>
                        </td>

                        {/* Provider Node & Identity */}
                        <td>
                          {file.provider_node_id ? (
                            <div>
                              <span className="node-badge">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                                  <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                                </svg>
                                Node #{file.provider_node_id}
                              </span>
                              <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary)' }}>
                                {file.provider_email || '—'}
                              </div>
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>

                        {/* Computer Name */}
                        <td>
                          {file.computer_name ? (
                            <span className="computer-badge" title={`Hostname: ${file.computer_name}`}>
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                                <line x1="8" y1="21" x2="16" y2="21" />
                                <line x1="12" y1="17" x2="12" y2="21" />
                              </svg>
                              {file.computer_name}
                            </span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>Unreported</span>
                          )}
                        </td>

                        {/* Chunk ID */}
                        <td>
                          {file.chunk_id ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                              <span className="chunk-mono" title={file.chunk_id}>
                                {file.chunk_id.substring(0, 16)}...
                              </span>
                              <button
                                type="button"
                                onClick={(e) => handleCopyChunkId(e, file.chunk_id)}
                                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
                                title="Copy Chunk ID"
                              >
                                {copiedChunkId === file.chunk_id ? (
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5">
                                    <polyline points="20 6 9 17 4 12" />
                                  </svg>
                                ) : (
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                  </svg>
                                )}
                              </button>
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>

                        {/* Encrypted Size */}
                        <td style={{ fontFamily: 'var(--font-mono)' }}>
                          <div>{formatBytes(file.encrypted_size)}</div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                            Raw: {formatBytes(file.file_size)}
                          </div>
                        </td>

                        {/* Storage Status */}
                        <td>
                          {renderStorageStatusBadge(file.storage_status)}
                        </td>

                        {/* Provider Connectivity */}
                        <td>
                          {file.provider_node_id ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                              <span
                                style={{
                                  width: '8px',
                                  height: '8px',
                                  borderRadius: '50%',
                                  backgroundColor: file.provider_online ? '#34d399' : '#f59e0b',
                                  boxShadow: file.provider_online ? '0 0 8px #34d399' : 'none',
                                }}
                              />
                              <span style={{ fontWeight: 600, color: file.provider_online ? '#34d399' : '#fbbf24' }}>
                                {file.provider_online ? 'Online' : 'Offline'}
                              </span>
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>

                        {/* Last Successful Storage Verification */}
                        <td style={{ fontSize: '0.78rem', color: file.last_verified_at ? '#cbd5e1' : 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                          {formatDateTime(file.last_verified_at)}
                        </td>

                        {/* Latest Relevant Job Status */}
                        <td>
                          {file.latest_delete_job ? (
                            <span className={`job-pill ${file.latest_delete_job.status.toLowerCase()}`}>
                              DEL: {file.latest_delete_job.status}
                            </span>
                          ) : file.latest_store_job ? (
                            <span className={`job-pill ${file.latest_store_job.status.toLowerCase()}`}>
                              STORE: {file.latest_store_job.status}
                            </span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)', fontSize: '0.76rem' }}>None</span>
                          )}
                        </td>

                        {/* Actions */}
                        <td style={{ textAlign: 'right' }}>
                          <button
                            type="button"
                            className="btn-action-icon btn-download"
                            onClick={(e) => {
                              e.stopPropagation()
                              setSelectedFile(file)
                            }}
                            id={`inspect-btn-${file.file_id}`}
                            title="Inspect physical chunk placement & jobs"
                          >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="11" cy="11" r="8" />
                              <line x1="21" y1="21" x2="16.65" y2="16.65" />
                            </svg>
                            <span>Inspect</span>
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* =====================================================================
          Chunk Placement & Storage Job History Inspection Modal
          ===================================================================== */}
      {selectedFile && (
        <div className="modal-overlay" onClick={() => setSelectedFile(null)}>
          <div
            className="tracking-modal-card"
            onClick={(e) => e.stopPropagation()}
            id="tracking-detail-modal"
          >
            {/* Modal Header */}
            <div className="tracking-modal-header">
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <h3 className="modal-title" style={{ fontSize: '1.35rem' }}>
                    {selectedFile.filename}
                  </h3>
                  {renderStorageStatusBadge(selectedFile.storage_status)}
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.84rem', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
                  File ID #{selectedFile.file_id} &bull; Owner: {selectedFile.consumer_email} (ID #{selectedFile.consumer_id}) &bull; Uploaded {formatDateTime(selectedFile.created_at)}
                </div>
              </div>
              <button
                type="button"
                className="modal-close"
                onClick={() => setSelectedFile(null)}
                id="close-modal-btn"
                title="Close inspection"
              >
                &times;
              </button>
            </div>

            {/* Modal Body */}
            <div className="tracking-modal-body">
              {/* Alert Warning if Provider Offline or Missing */}
              {selectedFile.storage_status === 'PROVIDER_OFFLINE' && (
                <div className="zk-alert-box" style={{ background: 'rgba(245, 158, 11, 0.1)', borderColor: 'rgba(245, 158, 11, 0.4)' }}>
                  <svg className="zk-alert-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" strokeWidth="2">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                    <line x1="12" y1="9" x2="12" y2="13" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                  </svg>
                  <div>
                    <div className="zk-alert-title" style={{ color: '#fbbf24' }}>
                      Provider Offline — Physical Presence Unverified
                    </div>
                    <div className="zk-alert-body">
                      The assigned provider node is currently offline or disconnected. StoreShare does not assume chunk retention while the provider host is unreachable.
                    </div>
                  </div>
                </div>
              )}

              {selectedFile.storage_status === 'PHYSICALLY_MISSING' && (
                <div className="zk-alert-box" style={{ background: 'rgba(239, 68, 68, 0.1)', borderColor: 'rgba(239, 68, 68, 0.4)' }}>
                  <svg className="zk-alert-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="15" y1="9" x2="9" y2="15" />
                    <line x1="9" y1="9" x2="15" y2="15" />
                  </svg>
                  <div>
                    <div className="zk-alert-title" style={{ color: '#f87171' }}>
                      Chunk Missing from Provider Disk!
                    </div>
                    <div className="zk-alert-body">
                      The provider machine is currently online, but the reported chunk ID was not found in the local agent filesystem inventory.
                    </div>
                  </div>
                </div>
              )}

              {/* Section 1: Physical Chunk-to-Provider Placement */}
              <div>
                <h4 className="detail-section-title">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                    <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                  </svg>
                  Physical Chunk Placement ({selectedFile.chunks?.length || 0} Chunks)
                </h4>

                {(!selectedFile.chunks || selectedFile.chunks.length === 0) ? (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.88rem', padding: '16px', background: 'rgba(255,255,255,0.03)', borderRadius: '8px' }}>
                    No physical chunks mapped to this file record.
                  </div>
                ) : (
                  <div className="chunks-grid">
                    {selectedFile.chunks.map((chk) => (
                      <div key={chk.chunk_id} className="chunk-card" id={`chunk-card-${chk.chunk_index}`}>
                        <div className="chunk-card-header">
                          <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                            Chunk #{chk.chunk_index} ({formatBytes(chk.chunk_size)})
                          </span>
                          {renderStorageStatusBadge(chk.storage_status)}
                        </div>

                        <div className="telemetry-box" style={{ margin: 0, padding: '12px' }}>
                          <div className="telemetry-row">
                            <span className="telemetry-label">Chunk ID:</span>
                            <span className="telemetry-value" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.76rem' }}>
                              {chk.chunk_id}
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Provider Node:</span>
                            <span className="telemetry-value">
                              Node #{chk.provider_node_id} ({chk.provider_email || 'Unassigned'})
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Host Computer:</span>
                            <span className="telemetry-value">
                              {chk.computer_name ? (
                                <span className="computer-badge">
                                  {chk.computer_name}
                                </span>
                              ) : (
                                <span style={{ color: 'var(--text-muted)' }}>Not reported</span>
                              )}
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Connectivity:</span>
                            <span className="telemetry-value" style={{ color: chk.provider_online ? '#34d399' : '#fbbf24' }}>
                              {chk.provider_status} ({chk.provider_online ? 'Online' : 'Offline'})
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Last Heartbeat:</span>
                            <span className="telemetry-value" style={{ fontSize: '0.76rem' }}>
                              {formatDateTime(chk.last_heartbeat)}
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Last Verified on Disk:</span>
                            <span className="telemetry-value" style={{ fontSize: '0.76rem', color: chk.last_verified_at ? '#34d399' : 'var(--text-muted)' }}>
                              {chk.last_verified_at ? formatDateTime(chk.last_verified_at) : 'Never verified'}
                            </span>
                          </div>

                          <div className="telemetry-row">
                            <span className="telemetry-label">Node Quota / Used:</span>
                            <span className="telemetry-value" style={{ fontSize: '0.76rem' }}>
                              {formatBytes(chk.used_bytes)} / {formatBytes(chk.quota_bytes)}
                            </span>
                          </div>
                        </div>

                        {/* Chunk Storage Jobs History */}
                        <div style={{ marginTop: '8px' }}>
                          <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                            Job History for Chunk #{chk.chunk_index}:
                          </div>
                          {(!chk.all_jobs || chk.all_jobs.length === 0) ? (
                            <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>No recorded jobs.</div>
                          ) : (
                            <table className="jobs-table">
                              <thead>
                                <tr>
                                  <th>Job ID</th>
                                  <th>Type</th>
                                  <th>Status</th>
                                  <th>Timestamp</th>
                                </tr>
                              </thead>
                              <tbody>
                                {chk.all_jobs.map((job) => (
                                  <tr key={job.id}>
                                    <td style={{ fontFamily: 'var(--font-mono)' }}>#{job.id}</td>
                                    <td style={{ fontWeight: 600 }}>{job.job_type}</td>
                                    <td>
                                      <span className={`job-pill ${job.status.toLowerCase()}`}>
                                        {job.status}
                                      </span>
                                    </td>
                                    <td style={{ fontFamily: 'var(--font-mono)' }}>
                                      {formatDateTime(job.updated_at || job.created_at)}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Modal Footer */}
            <div className="modal-footer" style={{ padding: '16px 28px', borderTop: '1px solid rgba(255, 255, 255, 0.08)' }}>
              <button
                type="button"
                className="action-btn"
                style={{ width: 'auto', padding: '8px 20px', background: 'rgba(255, 255, 255, 0.08)' }}
                onClick={() => setSelectedFile(null)}
              >
                Close Inspection
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
