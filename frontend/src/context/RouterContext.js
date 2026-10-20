import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const RouterContext = createContext(null)

export function RouterProvider({ children, initialPath }) {
  const [currentPath, setCurrentPath] = useState(() => {
    if (initialPath) return initialPath
    if (typeof window !== 'undefined' && window.location) {
      return window.location.pathname || '/'
    }
    return '/'
  })

  useEffect(() => {
    if (typeof window === 'undefined') return

    const handlePopState = () => {
      setCurrentPath(window.location.pathname || '/')
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const navigate = useCallback((path, options = {}) => {
    setCurrentPath((prev) => {
      if (path && path !== prev) {
        if (typeof window !== 'undefined') {
          if (options?.replace) {
            window.history.replaceState({}, '', path)
          } else {
            window.history.pushState({}, '', path)
          }
          if (typeof window.scrollTo === 'function') {
            window.scrollTo(0, 0)
          }
        }
        return path
      }
      return prev
    })
  }, [])

  const value = useMemo(() => ({ currentPath, navigate }), [currentPath, navigate])

  return createElement(RouterContext.Provider, { value }, children)
}

export function useRouter() {
  const context = useContext(RouterContext)
  if (!context) {
    throw new Error('useRouter must be used within a RouterProvider')
  }
  return context
}

/**
 * Declarative redirect component matching React Router's <Navigate>.
 * Executes navigation safely inside useEffect (post-render side effect)
 * preventing render-phase state update errors on parent providers.
 */
export function Navigate({ to, replace = true }) {
  const { navigate } = useRouter()

  useEffect(() => {
    if (to) {
      navigate(to, { replace })
    }
  }, [navigate, to, replace])

  return null
}
