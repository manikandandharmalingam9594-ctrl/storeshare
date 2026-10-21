/**
 * Authentication and API Client Service for Store-Share.
 *
 * TOKEN STORAGE APPROACH:
 * In this prototype, access tokens are stored in sessionStorage.
 * This provides session-scoped storage that is automatically cleared
 * when the browser tab or window is closed.
 *
 * SECURITY & XSS TRADEOFF:
 * Storing JWT tokens in browser storage (both sessionStorage and localStorage)
 * is vulnerable to Cross-Site Scripting (XSS) if malicious scripts execute
 * within the application context. Browser storage is NOT immune to XSS.
 * For production deployments with higher threat models, httpOnly, Secure,
 * SameSite cookies or short-lived memory-only tokens with secure refresh
 * rotations should be used instead.
 *
 * Passwords are NEVER logged or stored in browser storage.
 */

const TOKEN_KEY = 'storeshare_auth_token'

export const getToken = () => {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch (err) {
    console.error('Failed to read token from sessionStorage:', err)
    return null
  }
}

export const setToken = (token) => {
  try {
    if (token) {
      sessionStorage.setItem(TOKEN_KEY, token)
    } else {
      sessionStorage.removeItem(TOKEN_KEY)
    }
  } catch (err) {
    console.error('Failed to save token to sessionStorage:', err)
  }
}

export const clearToken = () => {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch (err) {
    console.error('Failed to clear token from sessionStorage:', err)
  }
}

/**
 * Safely parse API error responses from FastAPI.
 */
export async function parseApiError(response) {
  let message = `Request failed (HTTP ${response.status})`
  try {
    const errorData = await response.json()
    if (errorData && errorData.detail) {
      if (typeof errorData.detail === 'string') {
        message = errorData.detail
      } else if (Array.isArray(errorData.detail)) {
        // Pydantic validation error array
        message = errorData.detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
      } else {
        message = JSON.stringify(errorData.detail)
      }
    }
  } catch {
    // If response body is not JSON or is empty, use status text
    if (response.statusText) {
      message = `HTTP ${response.status}: ${response.statusText}`
    }
  }

  const error = new Error(message)
  error.status = response.status
  return error
}

export const authApi = {
  /**
   * Public Consumer Registration
   * Never passes or accepts a role field.
   */
  async register(email, password) {
    const response = await fetch('/api/auth/register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: email.trim(),
        password,
      }),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * User Login & Token Retrieval
   */
  async login(email, password) {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: email.trim(),
        password,
      }),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Fetch current authenticated user identity and role from backend
   */
  async getMe(customToken) {
    const token = customToken || getToken()
    if (!token) {
      throw new Error('No authentication token found.')
    }

    const response = await fetch('/api/auth/me', {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Admin-Only: Provision a new Storage Provider
   */
  async createProvider(email, password) {
    const token = getToken()
    if (!token) {
      throw new Error('Admin token required.')
    }

    const response = await fetch('/api/admin/providers', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({
        email: email.trim(),
        password,
      }),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Storage Provider Node APIs
   */
  async getNodeStatus() {
    const token = getToken()
    if (!token) {
      throw new Error('Authentication token required.')
    }

    const response = await fetch('/api/provider/node', {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  async setupNodeStorage(storage_dir, quota_gb, action = 'START') {
    const token = getToken()
    if (!token) {
      throw new Error('Authentication token required.')
    }

    const response = await fetch('/api/provider/node/setup', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({
        storage_dir: storage_dir.trim(),
        quota_gb: parseFloat(quota_gb),
        action,
      }),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Provider Physical Chunk Reconciliation API
   */
  async getProviderReconciliation() {
    const token = getToken()
    if (!token) {
      throw new Error('Authentication token required.')
    }

    const response = await fetch('/api/provider/reconciliation', {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Provider Asynchronous Job History & Health Telemetry API
   */
  async getProviderJobHistory(limit = 20) {
    const token = getToken()
    if (!token) {
      throw new Error('Authentication token required.')
    }

    const response = await fetch(`/api/provider/jobs/history?limit=${limit}`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Preserved System Diagnostics
   */
  async getHealth() {
    const response = await fetch('/api/health')
    if (!response.ok) {
      throw await parseApiError(response)
    }
    return await response.json()
  },

  async getTables() {
    const response = await fetch('/api/db/tables')
    if (!response.ok) {
      throw await parseApiError(response)
    }
    return await response.json()
  },

  /**
   * Admin-Only: Physical Storage Tracking
   */
  async getFileStorageTracking() {
    const token = getToken()
    if (!token) {
      throw new Error('Admin token required.')
    }

    const response = await fetch('/api/admin/files/storage', {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },
}
