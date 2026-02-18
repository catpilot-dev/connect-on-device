import { Navigate, Outlet, useNavigate } from 'react-router-dom'
import { useProfile } from '../api/queries'
import { isSignedIn, setAccessToken, signOut } from '../utils/helpers'
import { env } from '../utils/env'
import { DongleIdProvider } from '../utils/DongleIdContext'
import { useEffect } from 'react'

export const Component = () => {
  const navigate = useNavigate()
  const [_, { error }] = useProfile()

  useEffect(() => {
    if ((error as any)?.status !== 401) return
    signOut()
    navigate('/login')
  }, [error])

  // Auto-login in local mode — no auth needed on local network
  if (!isSignedIn() && env.MODE === 'device' && env.DEMO_ACCESS_TOKEN) {
    setAccessToken(env.DEMO_ACCESS_TOKEN)
  }

  if (!isSignedIn()) return <Navigate to="/login" />

  return (
    <DongleIdProvider>
      <div className="flex min-h-screen bg-background text-foreground">
        <div className="flex-1 flex flex-col min-w-0">
          <Outlet />
        </div>
      </div>
    </DongleIdProvider>
  )
}
