import { useEffect, useState } from 'react'
import { Route } from '../../types'
import { Icon } from '../../components/Icon'
import clsx from 'clsx'
import { useNavigate } from 'react-router-dom'
import { env } from '../../utils/env'

const ActionButton = ({
  icon,
  label,
  active,
  onClick,
  disabled,
  variant,
}: {
  icon: string
  label: string
  active?: boolean
  onClick: () => void
  disabled?: boolean
  variant?: 'danger'
}) => (
  <button
    onClick={onClick}
    disabled={disabled}
    className={clsx(
      'flex flex-1 flex-col items-center justify-center gap-2 rounded-xl p-3 transition-all active:scale-95',
      variant === 'danger'
        ? 'bg-red-500/15 text-red-400 hover:bg-red-500/25'
        : active
          ? 'bg-green-500 text-white'
          : 'bg-background-alt text-white hover:bg-background-alt/80',
      disabled && 'opacity-50 cursor-not-allowed',
    )}
  >
    <Icon name={icon as any} className={clsx('text-2xl', active ? 'text-white' : variant === 'danger' ? 'text-red-400' : 'text-white')} />
    <span className="text-xs font-medium">{label}</span>
  </button>
)

const DOWNLOAD_OPTIONS = [
  { key: 'rlog', label: 'rlog.zst', desc: 'Full logs' },
  { key: 'qcamera', label: 'qcamera.ts', desc: 'Low-res video' },
  { key: 'fcamera', label: 'fcamera.hevc', desc: 'Road camera' },
  { key: 'ecamera', label: 'ecamera.hevc', desc: 'Wide camera' },
  { key: 'qlog', label: 'qlog.zst', desc: 'Quantized logs' },
]

const DownloadModal = ({ route, onClose }: { route: Route; onClose: () => void }) => {
  const [selected, setSelected] = useState<Record<string, boolean>>({ rlog: true })
  const [allSegments, setAllSegments] = useState(true)
  const [segFrom, setSegFrom] = useState(0)
  const [segTo, setSegTo] = useState(route.maxqlog)

  const toggle = (key: string) => setSelected((s) => ({ ...s, [key]: !s[key] }))

  const handleDownload = () => {
    const files = Object.entries(selected)
      .filter(([, v]) => v)
      .map(([k]) => k)
      .join(',')
    if (!files) return
    const routeName = route.fullname.replace('/', '|')
    let url = `${env.API_URL}/v1/route/${routeName}/download?files=${files}`
    if (!allSegments) {
      const from = Math.max(0, Math.min(segFrom, route.maxqlog))
      const to = Math.max(from, Math.min(segTo, route.maxqlog))
      const segs = Array.from({ length: to - from + 1 }, (_, i) => from + i).join(',')
      url += `&segments=${segs}`
    }
    window.open(url)
    onClose()
  }

  const anySelected = Object.values(selected).some(Boolean)
  const segCount = allSegments ? route.maxqlog + 1 : Math.max(0, segTo - segFrom + 1)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl bg-[#1a1a2e] p-5 shadow-xl max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white">Download Route</h3>
          <button onClick={onClose} className="rounded-lg p-1 text-white/50 hover:bg-white/10 hover:text-white">
            <Icon name="close" className="text-xl" />
          </button>
        </div>

        <span className="text-xs font-bold uppercase tracking-wider text-white/40">File types</span>
        <div className="flex flex-col gap-2 mt-2">
          {DOWNLOAD_OPTIONS.map((opt) => (
            <label
              key={opt.key}
              className={clsx(
                'flex cursor-pointer items-center gap-3 rounded-xl p-3 transition-colors',
                selected[opt.key] ? 'bg-white/15' : 'bg-white/5 hover:bg-white/10',
              )}
            >
              <input
                type="checkbox"
                checked={!!selected[opt.key]}
                onChange={() => toggle(opt.key)}
                className="h-4 w-4 rounded accent-white"
              />
              <div className="flex flex-col">
                <span className="text-sm font-medium text-white">{opt.label}</span>
                <span className="text-xs text-white/40">{opt.desc}</span>
              </div>
            </label>
          ))}
        </div>

        <span className="text-xs font-bold uppercase tracking-wider text-white/40 mt-4 block">Segments</span>
        <div className="flex flex-col gap-2 mt-2">
          <label
            className={clsx(
              'flex cursor-pointer items-center gap-3 rounded-xl p-3 transition-colors',
              allSegments ? 'bg-white/15' : 'bg-white/5 hover:bg-white/10',
            )}
          >
            <input type="radio" checked={allSegments} onChange={() => setAllSegments(true)} className="h-4 w-4 accent-white" />
            <span className="text-sm font-medium text-white">All ({route.maxqlog + 1} segments)</span>
          </label>
          <label
            className={clsx(
              'flex cursor-pointer items-center gap-3 rounded-xl p-3 transition-colors',
              !allSegments ? 'bg-white/15' : 'bg-white/5 hover:bg-white/10',
            )}
          >
            <input type="radio" checked={!allSegments} onChange={() => setAllSegments(false)} className="h-4 w-4 accent-white" />
            <span className="text-sm font-medium text-white">Range</span>
          </label>
          {!allSegments && (
            <div className="flex items-center gap-2 px-3 pb-1">
              <input
                type="number"
                min={0}
                max={route.maxqlog}
                value={segFrom}
                onChange={(e) => setSegFrom(parseInt(e.target.value) || 0)}
                className="w-16 rounded-lg bg-white/10 px-2 py-1.5 text-sm text-white text-center outline-none focus:ring-1 focus:ring-white/30"
              />
              <span className="text-white/40 text-sm">to</span>
              <input
                type="number"
                min={0}
                max={route.maxqlog}
                value={segTo}
                onChange={(e) => setSegTo(parseInt(e.target.value) || 0)}
                className="w-16 rounded-lg bg-white/10 px-2 py-1.5 text-sm text-white text-center outline-none focus:ring-1 focus:ring-white/30"
              />
            </div>
          )}
        </div>

        <button
          onClick={handleDownload}
          disabled={!anySelected}
          className={clsx(
            'mt-4 w-full rounded-xl py-3 text-sm font-bold transition-colors',
            anySelected ? 'bg-white text-black hover:bg-white/90' : 'bg-white/10 text-white/30 cursor-not-allowed',
          )}
        >
          Download {segCount} segment{segCount !== 1 ? 's' : ''}
        </button>
      </div>
    </div>
  )
}

export const Actions = ({ route, className }: { route: Route; className?: string }) => {
  const navigate = useNavigate()
  const [isPreserved, setIsPreserved] = useState(false)
  const [showDownload, setShowDownload] = useState(false)

  const routeName = route.fullname.replace('/', '|')

  useEffect(() => {
    fetch(`${env.API_URL}/v1/route/${routeName}/`)
      .then((r) => r.json())
      .then((data) => {
        if (data.is_preserved !== undefined) setIsPreserved(data.is_preserved)
      })
      .catch(() => {})
  }, [routeName])

  const handlePreserve = async () => {
    const method = isPreserved ? 'DELETE' : 'POST'
    try {
      await fetch(`${env.API_URL}/v1/route/${routeName}/preserve`, { method })
      setIsPreserved(!isPreserved)
    } catch (e) {
      console.error('Preserve failed:', e)
    }
  }

  const handleDelete = async () => {
    if (!confirm('Hide this drive? It will be deleted when storage is low.')) return
    try {
      await fetch(`${env.API_URL}/v1/route/${routeName}/`, { method: 'DELETE' })
      navigate('/')
    } catch (e) {
      console.error('Delete failed:', e)
    }
  }

  return (
    <>
      <div className={clsx('grid grid-cols-3 gap-3', className)}>
        <ActionButton
          icon={isPreserved ? 'bookmark_check' : 'bookmark'}
          label={isPreserved ? 'Preserved' : 'Preserve'}
          active={isPreserved}
          onClick={handlePreserve}
        />
        <ActionButton icon="delete" label="Delete" onClick={handleDelete} variant="danger" />
        <ActionButton icon="download" label="Download" onClick={() => setShowDownload(true)} />
      </div>
      {showDownload && <DownloadModal route={route} onClose={() => setShowDownload(false)} />}
    </>
  )
}
