<script>
  import { onMount, onDestroy } from 'svelte'
  import Hls from 'hls.js'
  import { hudUrl, spriteUrl } from '../api.js'

  /**
   * Cross-browser HLS video player with HUD live stream support.
   *
   * Two video elements:
   * 1. HLS video (qcamera segments) — always loaded, hidden when HUD active
   * 2. HUD live stream video — shown when hudLiveUrl is set, plays live HLS from C3 compositor capture
   *
   * Compatibility strategy:
   * 1. hls.js (Chrome, Firefox, Edge, Opera, Android Chrome/Firefox/Samsung)
   * 2. Native HLS (Safari macOS, Safari iOS, Chrome iOS — all WebKit)
   * 3. Direct .ts fallback (single segment for any remaining edge cases)
   */

  /** @type {{ route: object, files: object, hudLiveUrl?: string|null, selectionStart?: number, selectionEnd?: number, currentTime?: number, duration?: number, onTimeUpdate?: (t: number) => void, onDurationChange?: (d: number) => void, onPlay?: () => void, onPause?: () => void }} */
  let {
    route,
    files,
    hudLiveUrl = null,
    frozen = false,
    selectionStart = 0,
    selectionEnd = 0,
    currentTime = $bindable(0),
    duration = $bindable(0),
    onTimeUpdate,
    onDurationChange,
    onPlay,
    onPause,
  } = $props()

  let videoEl = $state(null)
  let hudVideoEl = $state(null)
  let hls = null
  let hudHls = null
  let manifestUrl = null
  let isPlaying = $state(false)
  let isMuted = $state(true)
  let userWantsPause = false  // Guard against HLS spurious play events after seek

  // Track which video is active for control methods
  const showingHud = $derived(!!hudLiveUrl)
  const activeVideo = $derived(showingHud ? hudVideoEl : videoEl)

  const posterUrl = $derived(route ? spriteUrl(route, 0) : null)

  // Build M3U8 playlist from qcamera URLs
  function buildManifest(qcameraUrls) {
    const lines = [
      '#EXTM3U',
      '#EXT-X-VERSION:3',
      '#EXT-X-TARGETDURATION:61',
      '#EXT-X-MEDIA-SEQUENCE:0',
      '#EXT-X-PLAYLIST-TYPE:VOD',
    ]
    for (let i = 0; i < qcameraUrls.length; i++) {
      if (i > 0) lines.push('#EXT-X-DISCONTINUITY')
      const url = qcameraUrls[i]
      if (!url) {
        lines.push('#EXT-X-GAP', '#EXTINF:60.0,', 'gap')
      } else {
        lines.push('#EXTINF:60.0,', url)
      }
    }
    lines.push('#EXT-X-ENDLIST')
    return lines.join('\n')
  }

  function initPlayer() {
    if (!videoEl || !files?.qcameras) return

    cleanupHls()

    const manifest = buildManifest(files.qcameras)
    const blob = new Blob([manifest], { type: 'application/vnd.apple.mpegurl' })
    manifestUrl = URL.createObjectURL(blob)

    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        maxBufferLength: 30,
        maxMaxBufferLength: 120,
        startLevel: 0,
        fragLoadingTimeOut: 20000,
        fragLoadingMaxRetry: 6,
        levelLoadingTimeOut: 10000,
      })
      hls.loadSource(manifestUrl)
      hls.attachMedia(videoEl)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (!showingHud && !frozen) videoEl.play().catch(() => {})
      })

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.warn('HLS network error, attempting recovery...')
              hls.startLoad()
              break
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.warn('HLS media error, attempting recovery...')
              hls.recoverMediaError()
              break
            default:
              console.error('HLS fatal error:', data)
              hls.destroy()
              break
          }
        }
      })
    } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari native HLS — use server-side manifest (blob URLs not supported)
      const routeId = (route.local_id || route.fullname).replace('/', '|')
      videoEl.src = `/v1/route/${routeId}/manifest.m3u8`
      videoEl.addEventListener('loadedmetadata', () => {
        if (!showingHud) videoEl.play().catch(() => {})
      }, { once: true })
    } else {
      const firstUrl = files.qcameras?.find(u => u)
      if (firstUrl) {
        videoEl.src = firstUrl
        videoEl.addEventListener('loadedmetadata', () => {
          if (!showingHud) videoEl.play().catch(() => {})
        }, { once: true })
      }
    }
  }

  function cleanupHls() {
    if (hls) {
      hls.destroy()
      hls = null
    }
    if (manifestUrl) {
      URL.revokeObjectURL(manifestUrl)
      manifestUrl = null
    }
  }

  function cleanup() {
    cleanupHls()
    cleanupHudHls()
  }

  // ── HLS video event handlers ──────────────────────────────
  function handleHlsTimeUpdate() {
    if (!videoEl || showingHud) return
    currentTime = videoEl.currentTime
    onTimeUpdate?.(videoEl.currentTime)
  }

  function handleHlsDurationChange() {
    if (!videoEl || showingHud) return
    duration = videoEl.duration
    onDurationChange?.(videoEl.duration)
  }

  function handleHlsPlay() {
    if (showingHud) return
    if (frozen) { videoEl?.pause(); return }
    // HLS.js fires spurious play events after seeking across segments.
    // If user explicitly paused, suppress the auto-play.
    if (userWantsPause) { videoEl?.pause(); return }
    isPlaying = true
    onPlay?.()
  }

  function handleHlsPause() {
    if (showingHud) return
    isPlaying = false
    onPause?.()
  }

  // ── HUD live stream event handlers ───────────────────────
  function handleHudTimeUpdate() {
    // Time tracking handled by RouteDetailPage tick timer during live stream
  }

  function handleHudDurationChange() {
    // Keep duration from HLS (full route), not from live stream
  }

  function handleHudPlay() {
    if (!showingHud) return
    isPlaying = true
    onPlay?.()
  }

  function handleHudPause() {
    if (!showingHud) return
    isPlaying = false
    onPause?.()
  }

  function cleanupHudHls() {
    if (hudHls) {
      hudHls.destroy()
      hudHls = null
    }
  }

  // ── Swap logic: react to hudLiveUrl changes ───────────────
  $effect(() => {
    if (hudLiveUrl && hudVideoEl) {
      // Switching TO HUD live stream
      videoEl?.pause()

      cleanupHudHls()

      if (Hls.isSupported()) {
        hudHls = new Hls({
          enableWorker: true,
          liveSyncDurationCount: 2,
          liveMaxLatencyDurationCount: 5,
          maxBufferLength: 10,
          startLevel: 0,
        })
        hudHls.loadSource(hudLiveUrl)
        hudHls.attachMedia(hudVideoEl)
        hudHls.on(Hls.Events.MANIFEST_PARSED, () => {
          hudVideoEl.play().catch(() => {})
        })
        hudHls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
              hudHls.startLoad()
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
              hudHls.recoverMediaError()
            }
          }
        })
      } else if (hudVideoEl.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari native HLS
        hudVideoEl.src = hudLiveUrl
        hudVideoEl.addEventListener('loadedmetadata', () => {
          hudVideoEl.play().catch(() => {})
        }, { once: true })
      }
    } else if (!hudLiveUrl && videoEl) {
      // Switching FROM HUD back to qcamera HLS
      cleanupHudHls()
      if (hudVideoEl) {
        hudVideoEl.pause()
        hudVideoEl.removeAttribute('src')
        hudVideoEl.load()
      }
      // Resume qcamera at current time
      videoEl.currentTime = currentTime
      if (isPlaying) videoEl.play().catch(() => {})
    }
  })

  // Freeze: pause video immediately when frozen prop is set
  $effect(() => {
    if (frozen && videoEl && !videoEl.paused) {
      videoEl.pause()
    }
  })

  // React to files changing (route switch)
  $effect(() => {
    if (files) initPlayer()
  })

  onDestroy(cleanup)

  // ── Exported control methods — operate on active video ────
  export function seek(time) {
    if (showingHud) {
      // Live stream — seeking not supported, ignore
      return
    }
    if (videoEl) {
      const wasPlaying = !videoEl.paused
      userWantsPause = false  // Seeking implies user wants playback
      videoEl.currentTime = time
      // HLS.js may pause when seeking across segment discontinuities —
      // explicitly resume if video was playing (fixes loop-back stall)
      if (wasPlaying) videoEl.play().catch(() => {})
    }
  }

  export function play() {
    userWantsPause = false
    activeVideo?.play().catch(() => {})
  }

  export function pause() {
    userWantsPause = true
    activeVideo?.pause()
  }

  export function toggle() {
    const v = activeVideo
    if (!v) return
    if (v.paused) {
      userWantsPause = false
      v.play().catch(() => {})
    } else {
      userWantsPause = true
      v.pause()
    }
  }

  export function setPlaybackRate(rate) {
    if (videoEl) videoEl.playbackRate = rate
    if (hudVideoEl) hudVideoEl.playbackRate = rate
  }

  export function toggleMute() {
    isMuted = !isMuted
    if (videoEl) videoEl.muted = isMuted
    if (hudVideoEl) hudVideoEl.muted = isMuted
    return isMuted
  }

  export function getMuted() {
    return isMuted
  }
</script>

<div class="relative w-full">
  <!-- HLS video element — hidden when HUD video is active -->
  <video
    bind:this={videoEl}
    class="w-full block"
    class:hidden={showingHud}
    muted
    playsinline
    webkit-playsinline
    x5-video-player-type="h5"
    poster={posterUrl}
    preload="auto"
    ontimeupdate={handleHlsTimeUpdate}
    ondurationchange={handleHlsDurationChange}
    onplay={handleHlsPlay}
    onpause={handleHlsPause}
    onended={handleHlsPause}
  >
    Your browser does not support video playback.
  </video>

  <!-- HUD live stream video element — shown when hudLiveUrl is active -->
  <video
    bind:this={hudVideoEl}
    class="w-full block"
    class:hidden={!showingHud}
    muted
    playsinline
    webkit-playsinline
    x5-video-player-type="h5"
    preload="auto"
    ontimeupdate={handleHudTimeUpdate}
    ondurationchange={handleHudDurationChange}
    onplay={handleHudPlay}
    onpause={handleHudPause}
    onended={handleHudPause}
  >
  </video>

  <!-- Loading indicator when no video loaded -->
  {#if !files?.qcameras?.some(u => u)}
    <div class="absolute inset-0 flex items-center justify-center bg-surface-900">
      <p class="text-surface-400 text-sm">No video available</p>
    </div>
  {/if}
</div>
