import { createContext, useContext, useEffect, useState } from 'react'
import { authApi, clearToken, getToken, setToken } from '../services/auth'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [token, setTokenState] = useState(() => getToken())
  const [isLoading, setIsLoading] = useState(true)
  const [authError, setAuthError] = useState(null)

  // Validate existing token and restore session on mount
  useEffect(() => {
    let isMounted = true

    async function restoreSession() {
      const storedToken = getToken()
      if (!storedToken) {
        if (isMounted) {
          setIsLoading(false)
        }
        return
      }

      try {
        // Confirm live user identity and role from backend
        const me = await authApi.getMe(storedToken)
        if (isMounted) {
          setUser(me)
          setTokenState(storedToken)
          setAuthError(null)
        }
      } catch (err) {
        console.warn('Session restoration failed; clearing expired or invalid token:', err.message)
        clearToken()
        if (isMounted) {
          setUser(null)
          setTokenState(null)
        }
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    restoreSession()

    return () => {
      isMounted = false
    }
  }, [])

  /**
   * Log in user, store token in sessionStorage, and confirm identity via /api/auth/me
   */
  const login = async (email, password) => {
    setAuthError(null)
    const tokenResponse = await authApi.login(email, password)
    const accessToken = tokenResponse.access_token

    // Save token to sessionStorage
    setToken(accessToken)
    setTokenState(accessToken)

    // Call GET /api/auth/me to confirm live user identity and role from backend
    const verifiedUser = await authApi.getMe(accessToken)
    setUser(verifiedUser)
    return verifiedUser
  }

  /**
   * Register consumer and automatically authenticate session
   */
  const register = async (email, password) => {
    setAuthError(null)
    // 1. Create consumer record (role is strictly CONSUMER)
    await authApi.register(email, password)

    // 2. Automatically log in the newly registered consumer
    return await login(email, password)
  }

  /**
   * Clear session and log out
   */
  const logout = () => {
    clearToken()
    setUser(null)
    setTokenState(null)
    setAuthError(null)
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!user,
        isLoading,
        authError,
        setAuthError,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
