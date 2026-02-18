import { useStats } from '../../api/queries'
import { Slider } from '../../components/Slider'
import { formatDistance, formatDuration } from '../../utils/format'
import { useDongleId } from '../../utils/DongleIdContext'
import clsx from 'clsx'
import { useStorage } from '../../utils/storage'

export const Stats = ({ className }: { className?: string }) => {
  const dongleId = useDongleId()
  const [stats] = useStats(dongleId)
  const [timeRange, setTimeRange] = useStorage('statsTime')

  if (!stats) return null

  const s = stats[timeRange]

  const engagedPct =
    s.total_minutes_with_events && s.total_minutes_with_events > 0
      ? Math.round((s.engaged_minutes / s.total_minutes_with_events) * 100)
      : undefined

  const items = [
    { label: 'Distance', value: formatDistance(s.distance) ?? '0' },
    { label: 'Duration', value: formatDuration(s.minutes) ?? '0 min' },
    { label: 'Routes', value: s.routes.toString() },
    ...(engagedPct !== undefined ? [{ label: 'Engaged', value: `${engagedPct}%` }] : []),
  ]

  return (
    <div className={clsx('flex items-center gap-6', className)}>
      <Slider options={{ all: 'All', week: 'Last week' }} value={timeRange} onChange={setTimeRange} />
      {items.map(({ label, value }) => (
        <div key={label} className="flex flex-col items-start">
          <span className="text-xs text-white/40 font-medium">{label}</span>
          <span className="text-sm font-bold text-white leading-tight">{value}</span>
        </div>
      ))}
    </div>
  )
}
