import { create } from 'zustand'
import { getToken, getUserId, setToken, clearToken } from '@/shared/api/client'

interface AuthState {
  token: string | null
  userId: string | null
  isAuthenticated: boolean
  login: (token: string, userId: string) => void
  logout: () => void
  hydrate: () => void
}

export const useAuth = create<AuthState>((set) => ({
  token: null,
  userId: null,
  isAuthenticated: false,
  login: (token, userId) => {
    setToken(token, userId)
    set({ token, userId, isAuthenticated: true })
  },
  logout: () => {
    clearToken()
    set({ token: null, userId: null, isAuthenticated: false })
  },
  hydrate: () => {
    const token = getToken()
    const userId = getUserId()
    set({ token, userId, isAuthenticated: !!token })
  },
}))
