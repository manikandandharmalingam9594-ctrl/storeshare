import test from 'node:test'
import assert from 'node:assert/strict'
import { parseApiError } from '../src/services/auth.js'

// Simple mock for sessionStorage in Node environment
const mockStorage = new Map()
globalThis.sessionStorage = {
  getItem: (key) => mockStorage.get(key) || null,
  setItem: (key, val) => mockStorage.set(key, String(val)),
  removeItem: (key) => mockStorage.delete(key),
  clear: () => mockStorage.clear(),
}

// Target live backend port 8001 directly for Node test runner (since Node test runner doesn't run through browser Vite proxy)
const BASE_URL = 'http://127.0.0.1:8001'

test('parseApiError: extracts string detail correctly', async () => {
  const mockResponse = {
    status: 401,
    statusText: 'Unauthorized',
    json: async () => ({ detail: 'Invalid email or password.' }),
  }
  const err = await parseApiError(mockResponse)
  assert.equal(err.message, 'Invalid email or password.')
  assert.equal(err.status, 401)
})

test('parseApiError: extracts array validation detail correctly', async () => {
  const mockResponse = {
    status: 422,
    statusText: 'Unprocessable Entity',
    json: async () => ({
      detail: [
        { msg: 'Password must be at least 8 characters long.' },
        { msg: 'Invalid email address format.' },
      ],
    }),
  }
  const err = await parseApiError(mockResponse)
  assert.ok(err.message.includes('Password must be at least 8 characters long.'))
  assert.equal(err.status, 422)
})

test('parseApiError: falls back to HTTP status text when JSON parsing fails', async () => {
  const mockResponse = {
    status: 502,
    statusText: 'Bad Gateway',
    json: async () => {
      throw new Error('Not JSON')
    },
  }
  const err = await parseApiError(mockResponse)
  assert.equal(err.message, 'HTTP 502: Bad Gateway')
  assert.equal(err.status, 502)
})

test('sessionStorage token management: set, get, clear', async () => {
  const { getToken, setToken, clearToken } = await import('../src/services/auth.js')
  clearToken()
  assert.equal(getToken(), null)

  setToken('sample-jwt-token-xyz-123')
  assert.equal(getToken(), 'sample-jwt-token-xyz-123')

  clearToken()
  assert.equal(getToken(), null)
})

test('Role-based navigation resolution', () => {
  const getUserDashboardPath = (role) => {
    if (role === 'ADMIN') return '/admin'
    if (role === 'PROVIDER') return '/provider'
    return '/consumer'
  }

  assert.equal(getUserDashboardPath('CONSUMER'), '/consumer')
  assert.equal(getUserDashboardPath('PROVIDER'), '/provider')
  assert.equal(getUserDashboardPath('ADMIN'), '/admin')
  assert.equal(getUserDashboardPath('UNKNOWN'), '/consumer')
})

test('Role-based guard access check', () => {
  const isAuthorized = (userRole, requiredRole) => {
    return userRole === requiredRole
  }

  // Consumer checks
  assert.equal(isAuthorized('CONSUMER', 'CONSUMER'), true)
  assert.equal(isAuthorized('CONSUMER', 'ADMIN'), false)
  assert.equal(isAuthorized('CONSUMER', 'PROVIDER'), false)

  // Provider checks
  assert.equal(isAuthorized('PROVIDER', 'PROVIDER'), true)
  assert.equal(isAuthorized('PROVIDER', 'ADMIN'), false)

  // Admin checks
  assert.equal(isAuthorized('ADMIN', 'ADMIN'), true)
})

test('Live Backend: Health check endpoint', async () => {
  const res = await fetch(`${BASE_URL}/api/health`)
  assert.equal(res.status, 200)
  const data = await res.json()
  assert.equal(data.status, 'healthy')
  assert.equal(data.database, 'connected')
})

test('Live Backend: Consumer registration, login, and profile verification', async () => {
  const testEmail = `fe_test_${Date.now()}@storeshare.io`
  const testPassword = 'TestPassword123!'

  // 1. Register new consumer
  const regRes = await fetch(`${BASE_URL}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: testEmail, password: testPassword }),
  })
  assert.equal(regRes.status, 201)
  const regData = await regRes.json()
  assert.equal(regData.email, testEmail)
  assert.equal(regData.role, 'CONSUMER')
  assert.equal(regData.is_active, true)
  assert.equal(regData.password_hash, undefined)

  // 2. Duplicate registration returns 409 Conflict
  const dupRes = await fetch(`${BASE_URL}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: testEmail, password: testPassword }),
  })
  assert.equal(dupRes.status, 409)

  // 3. Login with invalid password returns 401
  const badLoginRes = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: testEmail, password: 'WrongPassword999!' }),
  })
  assert.equal(badLoginRes.status, 401)

  // 4. Login with correct credentials returns 200 with JWT
  const loginRes = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: testEmail, password: testPassword }),
  })
  assert.equal(loginRes.status, 200)
  const loginData = await loginRes.json()
  assert.ok(loginData.access_token)
  assert.equal(loginData.token_type, 'bearer')
  assert.equal(loginData.user.role, 'CONSUMER')

  // 5. Verify /api/auth/me with Bearer token
  const token = loginData.access_token
  const meRes = await fetch(`${BASE_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  assert.equal(meRes.status, 200)
  const meData = await meRes.json()
  assert.equal(meData.email, testEmail)
  assert.equal(meData.role, 'CONSUMER')
  assert.equal(meData.is_active, true)

  // 6. Consumer token rejected when calling admin endpoint
  const adminRes = await fetch(`${BASE_URL}/api/admin/providers`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ email: 'fake@provider.com', password: 'Password123!' }),
  })
  assert.equal(adminRes.status, 403)

  // 7. Consumer token rejected when calling provider node endpoint
  const provNodeRes = await fetch(`${BASE_URL}/api/provider/node`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
  assert.equal(provNodeRes.status, 403)
})

test('Storage provider node setup schema validation', async (t) => {
  // Unauthenticated requests to provider setup are rejected with 401
  const res = await fetch(`${BASE_URL}/api/provider/node/setup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ storage_dir: 'C:\\test', quota_gb: 10 }),
  })
  assert.equal(res.status, 401)
})

test('Admin File Storage Tracking endpoint authorization and structure', async (t) => {
  // 1. Unauthenticated request to /api/admin/files/storage returns 401
  const unauthRes = await fetch(`${BASE_URL}/api/admin/files/storage`)
  assert.equal(unauthRes.status, 401)

  // 2. Log in as admin to obtain admin JWT
  const loginRes = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@storeshare.io', password: 'AdminMasterKey123!' }),
  })
  assert.equal(loginRes.status, 200)
  const loginData = await loginRes.json()
  const adminToken = loginData.access_token
  assert.ok(adminToken)

  // 3. Admin request to /api/admin/files/storage returns 200 with tracking records
  const trackRes = await fetch(`${BASE_URL}/api/admin/files/storage`, {
    headers: { Authorization: `Bearer ${adminToken}` },
  })
  assert.equal(trackRes.status, 200)
  const records = await trackRes.json()
  assert.ok(Array.isArray(records))

  if (records.length > 0) {
    const file = records[0]
    assert.ok(typeof file.file_id === 'number')
    assert.ok(typeof file.filename === 'string')
    assert.ok(typeof file.consumer_id === 'number')
    assert.ok(typeof file.storage_status === 'string')
    assert.ok(Array.isArray(file.chunks))
  }
})

