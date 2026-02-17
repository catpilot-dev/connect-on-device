import { useEffect, useState } from 'react'
import { TopAppBar } from '../../components/TopAppBar'
import { BackButton } from '../../components/BackButton'
import { useDongleId } from '../../utils/DongleIdContext'
import { Preferences } from './Preferences'
import { env } from '../../utils/env'
import { Prime } from './Prime'

const DEVICE_NAMES: Record<string, string> = {
  tici: 'Comma 3',
  tize: 'Comma 3X',
  mici: 'Comma 4',
}

const InfoRow = ({ label, value }: { label: string; value?: string | null }) => {
  if (!value) return null
  return (
    <div className="flex items-center justify-between py-2 border-b border-white/5 last:border-0">
      <span className="text-xs text-white/60">{label}</span>
      <span className="font-mono text-sm">{value}</span>
    </div>
  )
}

export const Component = () => {
  const dongleId = useDongleId()
  const [deviceInfo, setDeviceInfo] = useState<{ device_type_raw?: string; agnos_version?: string }>({})

  useEffect(() => {
    fetch(`${env.API_URL}/v1/devices/${dongleId}/`)
      .then((r) => r.json())
      .then((data) => setDeviceInfo(data))
      .catch(() => {})
  }, [dongleId])

  const deviceLabel = deviceInfo.device_type_raw ? (DEVICE_NAMES[deviceInfo.device_type_raw] ?? deviceInfo.device_type_raw) : undefined

  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground">
      <TopAppBar leading={<BackButton href="/" />}>Settings</TopAppBar>
      <div className="flex flex-col gap-8 px-4 py-6 pb-20 max-w-2xl mx-auto w-full">
        <div className="flex flex-col gap-4">
          <h2 className="text-xl font-bold px-2">Device Info</h2>
          <div className="bg-background-alt rounded-xl p-4 flex flex-col">
            <InfoRow label="Dongle ID" value={dongleId} />
            <InfoRow label="Device" value={deviceLabel} />
            <InfoRow label="AGNOS" value={deviceInfo.agnos_version} />
          </div>
        </div>
        <Preferences />
        {!!env.BILLING_URL && <Prime />}
      </div>
    </div>
  )
}
