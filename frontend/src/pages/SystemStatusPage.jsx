import { useState } from 'react'
import { authApi } from '../services/auth'

export default function SystemStatusPage() {
  const [healthStatus, setHealthStatus] = useState('idle') // 'idle' | 'loading' | 'success' | 'error'
  const [healthData, setHealthData] = useState(null)
  const [healthError, setHealthError] = useState(null)
  const [healthTimestamp, setHealthTimestamp] = useState(null)
  const [showRawJson, setShowRawJson] = useState(false)

  const [dbStatus, setDbStatus] = useState('idle')
  const [dbData, setDbData] = useState(null)
  const [dbError, setDbError] = useState(null)

  const checkHealth = async () => {
    setHealthStatus('loading')
    setHealthError(null)

    try {
      const startTime = performance.now()
      const data = await authApi.getHealth()
      const latency = Math.round(performance.now() - startTime)

      setHealthData({ ...data, _latencyMs: latency })
      setHealthStatus('success')
      setHealthTimestamp(new Date().toLocaleTimeString())
    } catch (err) {
      console.error('Failed to check backend health:', err)
      setHealthStatus('error')
      setHealthError(
        err.message || 'Unable to connect to FastAPI backend at /api/health.'
      )
      setHealthTimestamp(new Date().toLocaleTimeString())
    }
  }

  const checkDbTables = async () => {
    setDbStatus('loading')
    setDbError(null)

    try {
      const data = await authApi.getTables()
      setDbData(data)
      setDbStatus('success')
    } catch (err) {
      console.error('Failed to query tables:', err)
      setDbStatus('error')
      setDbError(err.message || 'Failed to query /api/db/tables')
    }
  }

  return (
    <div>
      {/* Hero Section */}
      <section className="hero-section">
        <h2 className="hero-title">Decentralized Storage Mesh</h2>
        <p className="hero-subtitle">
          Core network foundation layer powered by FastAPI, SQLite metadata store, and React interface.
        </p>
      </section>

      {/* Main Content Grid */}
      <div className="dashboard-grid">
        {/* Card 1: Backend Health Check */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
                </svg>
              </div>
              <h3 className="card-title">Backend Health</h3>
            </div>
            <div className={`status-badge ${healthStatus}`}>
              <span className="status-dot" />
              <span>
                {healthStatus === 'idle' && 'Not Checked'}
                {healthStatus === 'loading' && 'Querying /api/health...'}
                {healthStatus === 'success' && 'Healthy'}
                {healthStatus === 'error' && 'Unavailable'}
              </span>
            </div>
          </div>

          <p className="card-desc">
            Pings the FastAPI backend via the Vite development proxy (<code>/api/health</code> &rarr; <code>http://127.0.0.1:8001/health</code>).
          </p>

          {/* Error Message */}
          {healthStatus === 'error' && (
            <div className="error-banner" id="health-error-banner">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <div>
                <div className="error-title">Backend Connection Failed</div>
                <div className="error-hint">{healthError}</div>
              </div>
            </div>
          )}

          {/* Success Telemetry */}
          {healthStatus === 'success' && healthData && (
            <div className="telemetry-box" id="health-telemetry-box">
              <div className="telemetry-row">
                <span className="telemetry-label">Status:</span>
                <span className="telemetry-value success">{healthData.status}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Service:</span>
                <span className="telemetry-value">{healthData.service}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">API Version:</span>
                <span className="telemetry-value">{healthData.version}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Database Status:</span>
                <span className="telemetry-value success">{healthData.database}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Database Path:</span>
                <span className="telemetry-value" style={{ fontSize: '0.78rem' }}>{healthData.database_path}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Round-trip Latency:</span>
                <span className="telemetry-value">{healthData._latencyMs} ms</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Last Checked:</span>
                <span className="telemetry-value">{healthTimestamp}</span>
              </div>
            </div>
          )}

          {/* Raw JSON toggle */}
          {healthData && (
            <div style={{ marginBottom: 16 }}>
              <button
                type="button"
                onClick={() => setShowRawJson(!showRawJson)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#38bdf8',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  textDecoration: 'underline',
                  padding: 0,
                }}
              >
                {showRawJson ? 'Hide Raw JSON Response' : 'View Raw JSON Response'}
              </button>
              {showRawJson && (
                <pre className="json-display" id="health-raw-json">
                  {JSON.stringify(healthData, null, 2)}
                </pre>
              )}
            </div>
          )}

          {/* Check Button */}
          <button
            id="check-health-btn"
            className="action-btn"
            onClick={checkHealth}
            disabled={healthStatus === 'loading'}
          >
            {healthStatus === 'loading' ? (
              <>
                <span className="spinner" />
                <span>Checking Node Health...</span>
              </>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="23 4 23 10 17 10" />
                  <polyline points="1 20 1 14 7 14" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
                <span>Check Backend Health</span>
              </>
            )}
          </button>
        </div>

        {/* Card 2: SQLite Database Schema Inspector */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <ellipse cx="12" cy="5" rx="9" ry="3" />
                  <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
                  <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                </svg>
              </div>
              <h3 className="card-title">SQLite Database</h3>
            </div>
            <div className={`status-badge ${dbStatus}`}>
              <span className="status-dot" />
              <span>
                {dbStatus === 'idle' && 'Unchecked'}
                {dbStatus === 'loading' && 'Querying...'}
                {dbStatus === 'success' && `${dbData?.count || 0} Tables`}
                {dbStatus === 'error' && 'Query Error'}
              </span>
            </div>
          </div>

          <p className="card-desc">
            Queries the read-only endpoint <code>/api/db/tables</code> to verify SQLite table existence without arbitrary SQL execution.
          </p>

          {/* Error Message */}
          {dbStatus === 'error' && (
            <div className="error-banner">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <div>
                <div className="error-title">Database Query Failed</div>
                <div className="error-hint">{dbError}</div>
              </div>
            </div>
          )}

          {/* Tables Info */}
          {dbStatus === 'success' && dbData && (
            <div className="telemetry-box" id="db-tables-box">
              <div className="telemetry-row">
                <span className="telemetry-label">Status:</span>
                <span className="telemetry-value success">{dbData.status}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Table Count:</span>
                <span className="telemetry-value">{dbData.count}</span>
              </div>
              <div className="telemetry-row">
                <span className="telemetry-label">Registered Tables:</span>
                <span className="telemetry-value success">
                  {dbData.tables.length > 0 ? dbData.tables.join(', ') : 'None'}
                </span>
              </div>
            </div>
          )}

          <button
            id="inspect-db-btn"
            className="action-btn secondary"
            onClick={checkDbTables}
            disabled={dbStatus === 'loading'}
          >
            {dbStatus === 'loading' ? (
              <>
                <span className="spinner" />
                <span>Inspecting Tables...</span>
              </>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <span>Inspect Database Tables</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Node Architecture Spec Footer */}
      <div className="architecture-card">
        <div className="architecture-header">
          <h4 className="arch-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
              <line x1="6" y1="6" x2="6.01" y2="6" />
              <line x1="6" y1="18" x2="6.01" y2="18" />
            </svg>
            <span>System Topology</span>
          </h4>
          <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Store-Share Node v0.2.0</span>
        </div>

        <div className="spec-grid">
          <div className="spec-item">
            <div className="spec-label">Backend Service</div>
            <div className="spec-val">FastAPI :8001</div>
          </div>
          <div className="spec-item">
            <div className="spec-label">Frontend Client</div>
            <div className="spec-val">React + Vite :3000</div>
          </div>
          <div className="spec-item">
            <div className="spec-label">Storage Engine</div>
            <div className="spec-val">SQLite (data/storeshare.db)</div>
          </div>
          <div className="spec-item">
            <div className="spec-label">Authentication Layer</div>
            <div className="spec-val">Argon2id + PyJWT (RBAC)</div>
          </div>
        </div>
      </div>
    </div>
  )
}
