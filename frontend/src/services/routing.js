/**
 * Route resolution types for StoreShare navigation decisions.
 */
export const RouteAction = {
  LOADING: 'LOADING',
  RENDER_STATUS: 'RENDER_STATUS',
  RENDER_LOGIN: 'RENDER_LOGIN',
  RENDER_REGISTER: 'RENDER_REGISTER',
  RENDER_CONSUMER: 'RENDER_CONSUMER',
  RENDER_PROVIDER: 'RENDER_PROVIDER',
  RENDER_ADMIN: 'RENDER_ADMIN',
  RENDER_ACCESS_DENIED: 'RENDER_ACCESS_DENIED',
  RENDER_NOT_FOUND: 'RENDER_NOT_FOUND',
  REDIRECT: 'REDIRECT',
}

/**
 * Returns the authorized dashboard route for an authenticated user role.
 * Fallbacks to '/consumer' for standard user accounts.
 */
export function getUserDashboardPath(role) {
  if (role === 'ADMIN') return '/admin'
  if (role === 'PROVIDER') return '/provider'
  return '/consumer'
}

/**
 * Pure route resolution function.
 * Evaluates route guards, authentication status, and role authorization
 * to determine the appropriate component rendering or redirect action.
 *
 * @param {Object} params
 * @param {string} params.currentPath - The current URL pathname
 * @param {boolean} params.isAuthenticated - Whether a valid session exists
 * @param {string|null|undefined} params.userRole - The authenticated user's role ('CONSUMER'|'PROVIDER'|'ADMIN')
 * @param {boolean} params.isLoading - Whether the initial session check is running
 * @returns {Object} Route resolution decision
 */
export function resolveRoute({ currentPath, isAuthenticated = false, userRole = null, isLoading = false }) {
  // 1. Session restoration loading state
  if (isLoading) {
    return { type: RouteAction.LOADING }
  }

  // 2. System Status / Diagnostics (publicly accessible)
  if (currentPath === '/' || currentPath === '/status') {
    return { type: RouteAction.RENDER_STATUS }
  }

  // 3. Login page (redirect authenticated users to their dashboard)
  if (currentPath === '/login') {
    if (isAuthenticated) {
      return {
        type: RouteAction.REDIRECT,
        redirectTo: getUserDashboardPath(userRole),
        replace: true,
      }
    }
    return { type: RouteAction.RENDER_LOGIN }
  }

  // 4. Registration page (redirect authenticated users to their dashboard)
  if (currentPath === '/register') {
    if (isAuthenticated) {
      return {
        type: RouteAction.REDIRECT,
        redirectTo: getUserDashboardPath(userRole),
        replace: true,
      }
    }
    return { type: RouteAction.RENDER_REGISTER }
  }

  // 5. Consumer Dashboard (requires authentication and CONSUMER role)
  if (currentPath === '/consumer') {
    if (!isAuthenticated) {
      return {
        type: RouteAction.REDIRECT,
        redirectTo: '/login',
        replace: true,
      }
    }
    if (userRole !== 'CONSUMER') {
      return {
        type: RouteAction.RENDER_ACCESS_DENIED,
        requiredRole: 'CONSUMER',
      }
    }
    return { type: RouteAction.RENDER_CONSUMER }
  }

  // 6. Provider Dashboard (requires authentication and PROVIDER role)
  if (currentPath === '/provider') {
    if (!isAuthenticated) {
      return {
        type: RouteAction.REDIRECT,
        redirectTo: '/login',
        replace: true,
      }
    }
    if (userRole !== 'PROVIDER') {
      return {
        type: RouteAction.RENDER_ACCESS_DENIED,
        requiredRole: 'PROVIDER',
      }
    }
    return { type: RouteAction.RENDER_PROVIDER }
  }

  // 7. Admin Dashboard (requires authentication and ADMIN role)
  if (currentPath === '/admin') {
    if (!isAuthenticated) {
      return {
        type: RouteAction.REDIRECT,
        redirectTo: '/login',
        replace: true,
      }
    }
    if (userRole !== 'ADMIN') {
      return {
        type: RouteAction.RENDER_ACCESS_DENIED,
        requiredRole: 'ADMIN',
      }
    }
    return { type: RouteAction.RENDER_ADMIN }
  }

  // 8. Unknown route fallback
  return { type: RouteAction.RENDER_NOT_FOUND }
}
