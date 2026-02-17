import clsx from 'clsx'
import { WIDTH, HEIGHT } from '../templates/shared'
import { CameraType, FileType, LogType } from '../types'
import { formatVideoTime, getRouteDurationMs, formatTime, getDateTime } from '../utils/format'
import { RefObject, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useAsyncMemo, useFullscreen, useRouteParams } from '../utils/hooks'
import { useNavigate } from 'react-router-dom'
import { useFiles, useRoute } from '../api/queries'
import { IconButton } from './IconButton'
import { getRouteUrl } from '../utils/helpers'
import { Icon } from './Icon'
import { getTimelineEvents, TimelineEvent } from '../utils/derived'
import { useStorage } from '../utils/storage'
import { Route } from '../types'
import { HlsPlayer, HlsPlayerRef } from './HlsPlayer'
import { ModelOverlay } from './ModelOverlay'
import type { FrameData } from '../log-reader/reader'

const FILE_LABELS: Record<FileType, string> = {
  cameras: 'Road',
  ecameras: 'Wide',
  dcameras: 'Driver',
  qcameras: 'Quantized',
  logs: 'Full',
  qlogs: 'Quantized',
}

const OptionItem = ({ label, selected, onClick, disabled }: { label: string; selected: boolean; onClick: () => void; disabled?: boolean }) => (
  <div
    className={clsx(
      'flex items-center gap-3 px-4 py-2 hover:bg-white/10 cursor-pointer text-sm transition-colors',
      disabled && 'opacity-50 pointer-events-none',
    )}
    onClick={onClick}
  >
    <Icon name="check" className={clsx('text-lg', !selected && 'invisible')} />
    <span>{label}</span>
  </div>
)

const TITLES = { large: 'Large Camera', small: 'Small Camera', log: 'Openpilot UI', rate: 'Playback Speed' }
const SettingsMenu = () => {
  const { routeName } = useRouteParams()
  const [route] = useRoute(routeName)
  const [files] = useFiles(routeName, route)
  const [view, setView] = useState<'large' | 'small' | 'log' | 'rate'>()
  const allCameras = CameraType.options.map((x) => ({ value: x, label: FILE_LABELS[x], disabled: !files || !files[x].some(Boolean) }))
  const allLogs = LogType.options.map((x) => ({ value: x, label: FILE_LABELS[x], disabled: !files || !files[x].some(Boolean) }))
  const onBack = () => setView(undefined)

  const title = view ? TITLES[view] : undefined

  const [largeCameraType, setLargeCameraType] = useStorage('largeCameraType')
  const [smallCameraType, setSmallCameraType] = useStorage('smallCameraType')
  const [logType, setLogType] = useStorage('logType')
  const [playbackRate, setPlaybackRate] = useStorage('playbackRate')
  const [showPath, setShowPath] = useStorage('showPath')

  return (
    <div className="absolute bottom-[120%] right-0 w-64 bg-[#1e1e1e]/95 backdrop-blur-sm border border-white/10 rounded-xl shadow-2xl overflow-hidden z-50 text-white animate-in fade-in slide-in-from-bottom-2 duration-200">
      {title && (
        <div className="flex items-center gap-2 px-2 py-2 border-b border-white/10 mb-1">
          <IconButton title="Back" name="arrow_back" className="text-xl" onClick={onBack} />
          <span className="text-sm font-medium">{title}</span>
        </div>
      )}

      {view === undefined && (
        <>
          {[
            { view: 'large' as const, value: FILE_LABELS[largeCameraType] },
            { view: 'small' as const, value: smallCameraType ? FILE_LABELS[smallCameraType] : 'Hidden' },
            { view: 'log' as const, value: logType ? FILE_LABELS[logType] : 'Hidden' },
            { view: 'rate' as const, value: `${playbackRate}x` },
          ].map(({ view, value }) => (
            <div
              key={view}
              className="flex items-center justify-between px-4 py-3 hover:bg-white/10 cursor-pointer text-sm transition-colors"
              onClick={() => setView(view)}
            >
              <span>{TITLES[view]}</span>
              <div className="flex items-center gap-1 text-white/70">
                <span>{value}</span>
                <Icon name="chevron_right" />
              </div>
            </div>
          ))}
          {logType && (
            <div
              className="flex items-center justify-between px-4 py-3 hover:bg-white/10 cursor-pointer text-sm transition-colors"
              onClick={() => setShowPath(!showPath)}
            >
              <span>Show Path</span>
              <div className={clsx('w-8 h-4 rounded-full transition-colors relative', showPath ? 'bg-green-500' : 'bg-white/30')}>
                <div className={clsx('absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform', showPath ? 'translate-x-4' : 'translate-x-0.5')} />
              </div>
            </div>
          )}
        </>
      )}

      {view === 'large' &&
        allCameras.map((option) => (
          <OptionItem
            key={option.value}
            label={option.label}
            selected={largeCameraType === option.value}
            disabled={option.disabled}
            onClick={() => {
              setLargeCameraType(option.value)
              setView(undefined)
            }}
          />
        ))}

      {view === 'small' &&
        [...allCameras, { value: 'none' as const, label: 'Hidden', disabled: false }].map((option) => (
          <OptionItem
            key={option.value}
            label={option.label}
            selected={(smallCameraType ?? 'none') === option.value}
            disabled={option.disabled}
            onClick={() => {
              setSmallCameraType(option.value === 'none' ? undefined : option.value)
              setView(undefined)
            }}
          />
        ))}

      {view === 'log' &&
        [...allLogs, { value: 'none' as const, label: 'Hidden', disabled: false }].map((option) => (
          <OptionItem
            key={option.value}
            label={option.label}
            selected={(logType ?? 'none') === option.value}
            disabled={option.disabled}
            onClick={() => {
              setLogType(option.value === 'none' ? undefined : option.value)
              setView(undefined)
            }}
          />
        ))}

      {view === 'rate' &&
        [0.25, 0.5, 1, 1.5, 2, 3, 4].map((rate) => (
          <OptionItem
            key={rate}
            label={`${rate}x`}
            selected={playbackRate === rate}
            onClick={() => {
              setPlaybackRate(rate)
              setView(undefined)
            }}
          />
        ))}
    </div>
  )
}

const getEventInfo = (event: TimelineEvent) => {
  if (event.type === 'engaged') return ['Engaged', 'bg-[#32CD32] min-w-[1px]', '1']
  if (event.type === 'overriding') return ['Overriding', 'bg-blue-500 min-w-[1px]', '2']
  if (event.type === 'user_flag') return ['User flag', 'bg-yellow-400 min-w-[2px]', '4']
  if (event.type === 'alert') {
    if (event.alertStatus === 1) return ['User prompt alert', 'bg-orange-500 min-w-[2px]', '3']
    else return ['Critical alert', 'bg-orange-500 min-w-[2px]', '3']
  }
  return ['Unknown', 'bg-gray-500', '0']
}

const Filmstrip = ({ route }: { route: Route }) => {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setWidth(entry.contentRect.width)
    })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])

  const imageCount = width ? Math.max(1, Math.round(width / 72)) : 16

  const images = useMemo(() => {
    const totalImages = route.maxqlog + 1
    return Array.from({ length: imageCount }).map((_, i) => {
      const index = Math.min(Math.floor(i * (totalImages / imageCount)), totalImages - 1)
      return {
        src: getRouteUrl(route, index, 'sprite.jpg'),
      }
    })
  }, [route, imageCount])

  return (
    <div
      ref={ref}
      className="absolute inset-0 grid h-full w-full pointer-events-none opacity-60"
      style={{ gridTemplateColumns: `repeat(${imageCount}, minmax(0, 1fr))` }}
    >
      {images.map((img, i) => (
        <div key={i} className="relative w-full h-full overflow-hidden bg-gray-900 border-r border-white/5 last:border-0">
          <img
            src={img.src}
            className="h-full w-full object-cover"
            loading="lazy"
            onError={(e) => {
              ;(e.target as HTMLImageElement).style.visibility = 'hidden'
            }}
          />
        </div>
      ))}
    </div>
  )
}

const Timeline = ({
  playerRef,
  currentTime,
  selection,
  onSelectionChange,
  duration,
  route,
}: {
  currentTime: number
  playerRef: React.RefObject<HlsPlayerRef | null>
  selection: { start: number; end: number }
  onSelectionChange: (sel: { start: number; end: number }) => void
  duration: number
  route?: Route
}) => {
  const events = useAsyncMemo(async () => (route ? await getTimelineEvents(route) : undefined), [route])
  const ref = useRef<HTMLDivElement>(null)

  const updateMarker = (clientX: number) => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const x = Math.min(Math.max(clientX - rect.left, 0), rect.width)
    playerRef.current?.seek((x / rect.width) * duration)
  }

  const [draggingHandle, setDraggingHandle] = useState<'start' | 'end' | null>(null)

  const updateHandle = (clientX: number, handleType?: 'start' | 'end') => {
    if (!onSelectionChange || !ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const x = Math.min(Math.max(clientX - rect.left, 0), rect.width)
    const time = (x / rect.width) * duration

    const activeHandle = handleType || draggingHandle

    if (activeHandle === 'start') {
      onSelectionChange({ ...selection, start: Math.min(time, selection.end - 1) })
      playerRef.current?.seek(time)
    } else if (activeHandle === 'end') {
      onSelectionChange({ ...selection, end: Math.max(time, selection.start + 1) })
      playerRef.current?.seek(time)
    }
  }

  const onStart = (handle?: 'start' | 'end') => {
    const onMouseMove = (ev: MouseEvent) => {
      if (handle) updateHandle(ev.clientX, handle)
      else updateMarker(ev.clientX)
    }
    const onTouchMove = (ev: TouchEvent) => {
      if (ev.cancelable) ev.preventDefault()
      if (ev.touches.length !== 1) return
      if (handle) updateHandle(ev.touches[0].clientX, handle)
      else updateMarker(ev.touches[0].clientX)
    }
    const onStop = () => {
      setDraggingHandle(null)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('mouseup', onStop)
      window.removeEventListener('touchend', onStop)
      window.removeEventListener('touchcancel', onStop)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('mouseup', onStop)
    window.addEventListener('touchend', onStop)
    window.addEventListener('touchcancel', onStop)
  }

  const markerOffset = duration > 0 ? (currentTime / duration) * 100 : 0
  const currentSegment = Math.floor(currentTime / 60)

  return (
    <div
      ref={ref}
      className={clsx(
        'relative h-14 w-full bg-black/20 rounded-lg overflow-visible cursor-pointer select-none group',
        'ring-1 ring-white/10 hover:ring-white/20 transition-all',
      )}
      onMouseDown={(ev) => {
        if (draggingHandle) return
        updateMarker(ev.clientX)
        onStart()
      }}
      onTouchStart={(ev) => {
        if (ev.touches.length !== 1) return
        if (draggingHandle) return
        updateMarker(ev.touches[0].clientX)
        onStart()
      }}
    >
      <div className="absolute inset-0 overflow-hidden rounded-lg">
        {route && <Filmstrip route={route} />}

        <div className="absolute inset-x-0 bottom-0 h-1.5 bg-black/40">
          {events?.map((event, i) => {
            const durationMs = duration * 1000
            const left = (event.route_offset_millis / durationMs) * 100
            const width = event.type === 'user_flag' ? (1000 / durationMs) * 100 : (event.end_route_offset_millis / durationMs) * 100 - left
            const [title, classes, zIndex] = getEventInfo(event)
            return (
              <div
                key={i}
                title={title}
                className={clsx('absolute top-0 h-full hover:brightness-150', classes)}
                style={{ left: `${left}%`, width: `${width}%`, zIndex }}
              />
            )
          })}
        </div>

        <div className="absolute inset-y-0 left-0 bg-black/60 pointer-events-none z-20" style={{ width: `${(selection.start / duration) * 100}%` }} />
        <div className="absolute inset-y-0 right-0 bg-black/60 pointer-events-none z-20" style={{ width: `${100 - (selection.end / duration) * 100}%` }} />

        <div
          className="absolute inset-y-0 border-x-2 border-white/50 z-20 pointer-events-none"
          style={{
            left: `${(selection.start / duration) * 100}%`,
            width: `${((selection.end - selection.start) / duration) * 100}%`,
          }}
        />
      </div>

      <div
        className="absolute top-0 bottom-0 z-10 w-0.5 bg-white shadow-[0_0_10px_rgba(255,255,255,0.5)] pointer-events-none"
        style={{ left: `${markerOffset}%` }}
      >
        <div className="absolute -top-1 -translate-x-1/2 w-3 h-3 bg-white rounded-full shadow-sm" />
        <div className="absolute -top-7 left-1/2 -translate-x-1/2 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono whitespace-nowrap opacity-100 transition-opacity pointer-events-none">
          {currentSegment}
        </div>
      </div>

      {['start', 'end'].map((type) => {
        const isStart = type === 'start'
        const val = isStart ? selection.start : selection.end
        const left = (val / duration) * 100
        const showLabel = val > 0 && val < duration
        if (Number.isNaN(left)) return null
        return (
          <div
            key={type}
            className="absolute top-1 bottom-1 w-4 -ml-2 z-30 cursor-ew-resize flex items-center justify-center group/handle"
            style={{ left: `${left}%` }}
            onMouseDown={(e) => {
              e.stopPropagation()
              setDraggingHandle(isStart ? 'start' : 'end')
              onStart(isStart ? 'start' : 'end')
            }}
            onTouchStart={(e) => {
              e.stopPropagation()
              setDraggingHandle(isStart ? 'start' : 'end')
              onStart(isStart ? 'start' : 'end')
            }}
          >
            <div
              className={clsx(
                'w-1 h-full bg-white rounded-full shadow-lg transition-transform group-hover/handle:scale-x-150',
                isStart ? 'translate-x-0.5' : '-translate-x-0.5',
              )}
            />

            <div
              className={clsx(
                'absolute -top-7 left-1/2 -translate-x-1/2 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono whitespace-nowrap opacity-100 transition-opacity pointer-events-none',
                !showLabel && 'hidden',
              )}
            >
              {formatVideoTime(Math.round(val))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const VideoControls = ({ playerRef, className }: { className?: string; playerRef: RefObject<HlsPlayerRef | null> }) => {
  const fullscreen = useFullscreen()
  const { routeName, start, end, date } = useRouteParams()
  const [playing, setPlaying] = useState(true)
  const [muted, setMuted] = useState(true)
  const [currentTime, setCurrentTime] = useState(0)
  const settingsRef = useRef<HTMLDivElement>(null)
  const [route] = useRoute(routeName)
  const [showSettings, setShowSettings] = useState(false)
  const navigate = useNavigate()
  const duration = (getRouteDurationMs(route) ?? 0) / 1000
  const selection = useMemo(
    () => ({
      start: start ?? 0,
      end: end ?? duration,
    }),
    [start, end, duration],
  )

  const onSelectionChange = (sel: { start: number; end: number }) => {
    if (Math.abs(sel.start) < 1 && Math.abs(sel.end - duration) < 1) navigate(`/${date}`, { replace: true })
    else navigate(`/${date}/${sel.start.toFixed(0)}/${sel.end.toFixed(0)}`, { replace: true })
  }

  useEffect(() => {
    const video = playerRef.current?.getVideoElement()
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      setCurrentTime(t)
      // Clamp playback within selection bounds
      if (t >= selection.end) {
        video.currentTime = selection.start
      } else if (t < selection.start - 0.5) {
        video.currentTime = selection.start
      }
    }
    const onPlayPause = () => setPlaying(!video.paused)
    const onVolumeChange = () => setMuted(video.muted)

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('play', onPlayPause)
    video.addEventListener('pause', onPlayPause)
    video.addEventListener('volumechange', onVolumeChange)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('play', onPlayPause)
      video.removeEventListener('pause', onPlayPause)
      video.removeEventListener('volumechange', onVolumeChange)
    }
  }, [playerRef.current, selection])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(event.target as Node)) {
        setShowSettings(false)
      }
    }
    if (showSettings) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showSettings])

  return (
    <div className={clsx('flex flex-col gap-2 p-2 bg-black/20 rounded-xl backdrop-blur-md border border-white/5', className)}>
      <Timeline playerRef={playerRef} currentTime={currentTime} selection={selection} onSelectionChange={onSelectionChange} duration={duration} route={route} />

      <div className="flex items-center gap-2 pt-2">
        <IconButton title={playing ? 'Pause' : 'Play'} name={playing ? 'pause' : 'play_arrow'} onClick={() => playerRef.current?.toggle()} />
        <IconButton
          title={muted ? 'Unmute' : 'Mute'}
          name={muted ? 'volume_off' : 'volume_up'}
          onClick={() => (muted ? playerRef.current?.unmute() : playerRef.current?.mute())}
        />

        <span className="text-sm font-mono opacity-80 min-w-[100px]">
          {formatVideoTime(Math.round(currentTime))} / {formatVideoTime(Math.round(duration))}
        </span>

        <div className="flex-1" />

        <div className="relative flex items-center justify-center" ref={settingsRef}>
          {showSettings && <SettingsMenu />}
          <IconButton title="Settings" name="settings" onClick={() => setShowSettings(!showSettings)} />
        </div>
        <IconButton
          title={fullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          name={fullscreen ? 'fullscreen_exit' : 'fullscreen'}
          onClick={() => {
            if (fullscreen) document.exitFullscreen()
            else document.querySelector('#fullscreen')?.requestFullscreen()
          }}
        />
      </div>
    </div>
  )
}

export const RouteVideoPlayer = ({ playerRef, className }: { playerRef: RefObject<HlsPlayerRef | null>; className?: string }) => {
  const { routeName, start } = useRouteParams()
  const [route] = useRoute(routeName)
  const [files] = useFiles(routeName, route)

  const duration = (getRouteDurationMs(route) ?? 0) / 1000

  const [playbackRate] = useStorage('playbackRate')
  const [logType] = useStorage('logType')
  const [showPath] = useStorage('showPath')

  const qcameraUrls = useMemo(() => {
    if (!files) return []
    return files.qcameras
  }, [files])

  const [currentTime, setCurrentTime] = useState<string>()
  useEffect(() => {
    const interval = setInterval(() => {
      if (!playerRef.current || !route) return
      const seconds = playerRef.current.getCurrentTime()
      const time = getDateTime(route.start_time)?.plus({ seconds })
      if (time) setCurrentTime(formatTime(time, true))
    }, 500)
    return () => clearInterval(interval)
  }, [route])

  // Log overlay state
  const [logFrames, setLogFrames] = useState<Record<string, FrameData> | null>(null)
  const loadedSegRef = useRef(-1)
  const workerRef = useRef<Worker | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 })

  // Track container size for canvas dimensions
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(([e]) => {
      setCanvasSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Load log data when segment changes
  const checkAndLoadSegment = useCallback(() => {
    if (!logType || !files) {
      return
    }
    const sec = playerRef.current?.getCurrentTime() ?? 0
    const seg = Math.floor(sec / 60)
    if (seg === loadedSegRef.current) return

    const url = files[logType]?.[seg]
    if (!url) return

    loadedSegRef.current = seg
    setLogFrames(null)

    // Terminate previous worker
    workerRef.current?.terminate()

    const worker = new Worker(new URL('../log-reader/worker.ts', import.meta.url), { type: 'module' })
    workerRef.current = worker
    worker.postMessage({ url })
    worker.onmessage = ({ data }) => {
      if (data.frames) setLogFrames(data.frames)
      worker.terminate()
      if (workerRef.current === worker) workerRef.current = null
    }
  }, [logType, files, playerRef])

  useEffect(() => {
    checkAndLoadSegment()
    const interval = setInterval(checkAndLoadSegment, 2000)
    return () => {
      clearInterval(interval)
      workerRef.current?.terminate()
      workerRef.current = null
    }
  }, [checkAndLoadSegment])

  // Reset when logType is cleared
  useEffect(() => {
    if (!logType) {
      setLogFrames(null)
      loadedSegRef.current = -1
    }
  }, [logType])

  return (
    <div ref={containerRef} id="fullscreen" className={clsx('relative rounded-xl overflow-hidden bg-black', className)} style={{ aspectRatio: WIDTH / HEIGHT }}>
      <HlsPlayer ref={playerRef} qcameraUrls={qcameraUrls} autoPlay initialTime={start ?? 0} playbackRate={playbackRate} className="w-full h-full" />

      {logType && logFrames && canvasSize.w > 0 && (
        <ModelOverlay
          playerRef={playerRef}
          logFrames={logFrames}
          canvasWidth={canvasSize.w}
          canvasHeight={canvasSize.h}
          showPath={showPath}
        />
      )}

      <div className="absolute inset-0 cursor-pointer" onClick={() => playerRef.current?.toggle()} />

      {currentTime && (
        <div className="absolute top-4 right-4 bg-black/50 text-white px-2 py-1 rounded text-sm font-mono pointer-events-none">{currentTime}</div>
      )}
    </div>
  )
}
