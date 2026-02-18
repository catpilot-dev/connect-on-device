import { useEffect } from 'react'
import { Loading } from '../../components/Loading'
import { useDevice } from '../../api/queries'
import { Routes } from './Routes'
import { Stats } from './Stats'
import { Navigation } from './Navigation'
import { useDongleId } from '../../utils/DongleIdContext'
import { Icon } from '../../components/Icon'
import { useStorage } from '../../utils/storage'
import { useDeviceParams } from './useDeviceParams'

export const Component = () => {
  const dongleId = useDongleId()
  const [device, { isLoading, error }] = useDevice(dongleId)
  const [usingCorrectFork] = useStorage('usingCorrectFork')
  const load = useDeviceParams((s) => s.load)

  useEffect(() => {
    if (usingCorrectFork && dongleId) load(dongleId)
  }, [dongleId, usingCorrectFork, load])

  if (isLoading) return <Loading className="h-screen w-screen" />

  if (!device || error) {
    return (
      <div className="flex flex-1 w-full flex-col items-center justify-center gap-6 bg-background text-background-x p-4">
        <div className="flex flex-col items-center gap-2 text-center">
          <Icon name="error" className="text-error text-5xl" />
          <h1 className="text-2xl font-bold text-primary">Device not found</h1>
          <p className="text-secondary-alt-x">The device you are looking for does not exist or you don't have permission to view it.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen bg-background">
      <div className="flex flex-col gap-4 p-6 max-w-screen-lg mx-auto w-full">
        <div className="flex items-center justify-between">
          <Stats />
          <Navigation />
        </div>
        <Routes className="" />
      </div>
    </div>
  )
}
