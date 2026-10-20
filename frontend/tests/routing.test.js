import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, useRouter, Navigate } from '../src/context/RouterContext.js'
import { resolveRoute, RouteAction, getUserDashboardPath } from '../src/services/routing.js'

test('Routing: getUserDashboardPath returns correct dashboard paths', () => {
  assert.equal(getUserDashboardPath('ADMIN'), '/admin')
  assert.equal(getUserDashboardPath('PROVIDER'), '/provider')
  assert.equal(getUserDashboardPath('CONSUMER'), '/consumer')
  assert.equal(getUserDashboardPath('UNKNOWN'), '/consumer')
  assert.equal(getUserDashboardPath(null), '/consumer')
  assert.equal(getUserDashboardPath(undefined), '/consumer')
})

test('Routing: Loading state takes precedence during session restoration', () => {
  const result = resolveRoute({
    currentPath: '/consumer',
    isAuthenticated: false,
    userRole: null,
    isLoading: true,
  })
  assert.equal(result.type, RouteAction.LOADING)
})

test('Routing: Public diagnostics routes accessible without authentication', () => {
  const homeResult = resolveRoute({
    currentPath: '/',
    isAuthenticated: false,
    isLoading: false,
  })
  assert.equal(homeResult.type, RouteAction.RENDER_STATUS)

  const statusResult = resolveRoute({
    currentPath: '/status',
    isAuthenticated: false,
    isLoading: false,
  })
  assert.equal(statusResult.type, RouteAction.RENDER_STATUS)
})

test('Routing: Unauthenticated users visiting protected routes are redirected to /login', () => {
  const protectedRoutes = ['/consumer', '/provider', '/admin']

  for (const path of protectedRoutes) {
    const decision = resolveRoute({
      currentPath: path,
      isAuthenticated: false,
      userRole: null,
      isLoading: false,
    })

    assert.equal(decision.type, RouteAction.REDIRECT, `Path ${path} must redirect when unauthenticated`)
    assert.equal(decision.redirectTo, '/login')
    assert.equal(decision.replace, true)
  }
})

test('Routing: Unauthenticated users can view login and registration forms', () => {
  const loginDecision = resolveRoute({
    currentPath: '/login',
    isAuthenticated: false,
    isLoading: false,
  })
  assert.equal(loginDecision.type, RouteAction.RENDER_LOGIN)

  const registerDecision = resolveRoute({
    currentPath: '/register',
    isAuthenticated: false,
    isLoading: false,
  })
  assert.equal(registerDecision.type, RouteAction.RENDER_REGISTER)
})

test('Routing: Authenticated users on /login and /register are redirected to authorized dashboard', () => {
  const roles = [
    { role: 'CONSUMER', expectedPath: '/consumer' },
    { role: 'PROVIDER', expectedPath: '/provider' },
    { role: 'ADMIN', expectedPath: '/admin' },
  ]

  for (const { role, expectedPath } of roles) {
    // Check /login redirect
    const loginDecision = resolveRoute({
      currentPath: '/login',
      isAuthenticated: true,
      userRole: role,
      isLoading: false,
    })
    assert.equal(loginDecision.type, RouteAction.REDIRECT)
    assert.equal(loginDecision.redirectTo, expectedPath)
    assert.equal(loginDecision.replace, true)

    // Check /register redirect
    const registerDecision = resolveRoute({
      currentPath: '/register',
      isAuthenticated: true,
      userRole: role,
      isLoading: false,
    })
    assert.equal(registerDecision.type, RouteAction.REDIRECT)
    assert.equal(registerDecision.redirectTo, expectedPath)
    assert.equal(registerDecision.replace, true)
  }
})

test('Routing: Authenticated Consumer can access /consumer, blocked on /provider and /admin', () => {
  // Authorized route
  const consumerDecision = resolveRoute({
    currentPath: '/consumer',
    isAuthenticated: true,
    userRole: 'CONSUMER',
    isLoading: false,
  })
  assert.equal(consumerDecision.type, RouteAction.RENDER_CONSUMER)

  // Blocked on provider route
  const providerDecision = resolveRoute({
    currentPath: '/provider',
    isAuthenticated: true,
    userRole: 'CONSUMER',
    isLoading: false,
  })
  assert.equal(providerDecision.type, RouteAction.RENDER_ACCESS_DENIED)
  assert.equal(providerDecision.requiredRole, 'PROVIDER')

  // Blocked on admin route
  const adminDecision = resolveRoute({
    currentPath: '/admin',
    isAuthenticated: true,
    userRole: 'CONSUMER',
    isLoading: false,
  })
  assert.equal(adminDecision.type, RouteAction.RENDER_ACCESS_DENIED)
  assert.equal(adminDecision.requiredRole, 'ADMIN')
})

test('Routing: Authenticated Provider can access /provider, blocked on /consumer and /admin', () => {
  // Authorized route
  const providerDecision = resolveRoute({
    currentPath: '/provider',
    isAuthenticated: true,
    userRole: 'PROVIDER',
    isLoading: false,
  })
  assert.equal(providerDecision.type, RouteAction.RENDER_PROVIDER)

  // Blocked on consumer route
  const consumerDecision = resolveRoute({
    currentPath: '/consumer',
    isAuthenticated: true,
    userRole: 'PROVIDER',
    isLoading: false,
  })
  assert.equal(consumerDecision.type, RouteAction.RENDER_ACCESS_DENIED)
  assert.equal(consumerDecision.requiredRole, 'CONSUMER')

  // Blocked on admin route
  const adminDecision = resolveRoute({
    currentPath: '/admin',
    isAuthenticated: true,
    userRole: 'PROVIDER',
    isLoading: false,
  })
  assert.equal(adminDecision.type, RouteAction.RENDER_ACCESS_DENIED)
  assert.equal(adminDecision.requiredRole, 'ADMIN')
})

test('Routing: Authenticated Admin can access /admin, blocked on other role dashboards', () => {
  const adminDecision = resolveRoute({
    currentPath: '/admin',
    isAuthenticated: true,
    userRole: 'ADMIN',
    isLoading: false,
  })
  assert.equal(adminDecision.type, RouteAction.RENDER_ADMIN)

  const consumerDecision = resolveRoute({
    currentPath: '/consumer',
    isAuthenticated: true,
    userRole: 'ADMIN',
    isLoading: false,
  })
  assert.equal(consumerDecision.type, RouteAction.RENDER_ACCESS_DENIED)
  assert.equal(consumerDecision.requiredRole, 'CONSUMER')
})

test('Routing: Unknown routes resolve to RENDER_NOT_FOUND', () => {
  const notFoundDecision = resolveRoute({
    currentPath: '/nonexistent-route-xyz',
    isAuthenticated: false,
    isLoading: false,
  })
  assert.equal(notFoundDecision.type, RouteAction.RENDER_NOT_FOUND)
})

test('RouterProvider and Navigate: renders cleanly without render-phase setState warnings', () => {
  // Intercept console.error to assert zero React warnings
  const originalError = console.error
  const capturedErrors = []
  console.error = (...args) => {
    capturedErrors.push(args.join(' '))
    originalError(...args)
  }

  try {
    function ConsumerComponent() {
      const router = useRouter()
      return createElement('div', { id: 'test-content' }, `Path: ${router.currentPath}`)
    }

    const html = renderToStaticMarkup(
      createElement(RouterProvider, { initialPath: '/login' },
        createElement(ConsumerComponent, null),
        createElement(Navigate, { to: '/consumer', replace: true })
      )
    )

    assert.ok(html.includes('Path: /login'))

    // Confirm NO React warnings occurred during render
    const setStateInRenderErrors = capturedErrors.filter(err =>
      err.includes('Cannot update a component') || err.includes('while rendering a different component')
    )
    assert.equal(setStateInRenderErrors.length, 0, 'No render-time setState warnings must occur')
  } finally {
    console.error = originalError
  }
})

test('useRouter: throws descriptive error when used outside RouterProvider', () => {
  function BadComponent() {
    useRouter()
    return null
  }

  assert.throws(
    () => renderToStaticMarkup(createElement(BadComponent)),
    /useRouter must be used within a RouterProvider/
  )
})
