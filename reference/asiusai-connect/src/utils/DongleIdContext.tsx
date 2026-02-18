import { createContext, useContext } from 'react'
import { useDevices } from '../api/queries'

const DongleIdContext = createContext<string>('')

export const DongleIdProvider = ({ children }: { children: React.ReactNode }) => {
  const [devices] = useDevices()
  const dongleId = devices?.[0]?.dongle_id ?? ''
  return <DongleIdContext.Provider value={dongleId}>{children}</DongleIdContext.Provider>
}

export const useDongleId = () => useContext(DongleIdContext)
