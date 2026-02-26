<script>
  import { onMount, onDestroy } from 'svelte'
  import Hls from 'hls.js'
  import { hudUrl, spriteUrl, cameraUrl } from '../api.js'

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

  /** @type {{ route: object, files: object, hudLiveUrl?: string|null, hdSource?: string|null, selectionStart?: number, selectionEnd?: number, currentTime?: number, duration?: number, onTimeUpdate?: (t: number) => void, onDurationChange?: (d: number) => void, onPlay?: () => void, onPause?: () => void }} */
  let {
    route,
    files,
    hudLiveUrl = null,
    hdSource = null,
    frozen = false,
    selectionStart = 0,
    selectionEnd = 0,
    currentTime = $bindable(0),
    duration = $bindable(0),
    onTimeUpdate,
    onDurationChange,
    onPlay,
    onPause,
    onHudStream,
    onHudDownload,
    onHevcFailed,
  } = $props()

  let videoEl = $state(null)
  let hudVideoEl = $state(null)
  let hdVideoEl = $state(null)
  let hls = null
  let hudHls = null
  let manifestUrl = null
  let isPlaying = $state(false)
  let isMuted = $state(true)
  let buffering = $state(true)   // Show spinner until first frame ready
  let userWantsPause = false  // Guard against HLS spurious play events after seek

  // HD (fcamera) state
  let hdSegment = $state(-1)          // currently loaded HD segment

  // Track which video is active for control methods
  const showingHud = $derived(!!hudLiveUrl)
  const showingHd = $derived(!!hdSource && !showingHud)
  const activeVideo = $derived(showingHud ? hudVideoEl : showingHd ? hdVideoEl : videoEl)

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

    buffering = true
    cleanupHls()

    const manifest = buildManifest(files.qcameras)
    const blob = new Blob([manifest], { type: 'application/vnd.apple.mpegurl' })
    manifestUrl = URL.createObjectURL(blob)

    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        maxBufferLength: 60,
        maxMaxBufferLength: 180,
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
    if (!videoEl || showingHud || showingHd) return
    currentTime = videoEl.currentTime
    onTimeUpdate?.(videoEl.currentTime)
  }

  function handleHlsDurationChange() {
    if (!videoEl || showingHud) return
    // Always set duration from HLS — it knows total route length from manifest.
    // HD player only loads individual 60s segments and can't determine total duration.
    duration = videoEl.duration
    onDurationChange?.(videoEl.duration)
  }

  function handleHlsPlay() {
    if (showingHud || showingHd) return
    if (frozen) { videoEl?.pause(); return }
    // HLS.js fires spurious play events after seeking across segments.
    // If user explicitly paused, suppress the auto-play.
    if (userWantsPause) { videoEl?.pause(); return }
    isPlaying = true
    onPlay?.()
  }

  function handleHlsPause() {
    if (showingHud || showingHd) return
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

  // ── HD (fcamera) playback ────────────────────────────────
  const maxSegment = $derived(files?.qcameras ? files.qcameras.length - 1 : 0)

  function loadHdSegment(seg, seekOffset = 0, autoPlay = false) {
    if (!hdVideoEl || !route || !hdSource) return
    if (seg < 0 || seg > maxSegment) return

    hdSegment = seg
    const url = cameraUrl(route.local_id, hdSource, seg)
    hdVideoEl.src = url
    hdVideoEl.load()
    hdVideoEl.addEventListener('loadedmetadata', () => {
      if (seekOffset > 0) hdVideoEl.currentTime = seekOffset
      if (autoPlay) hdVideoEl.play().catch(() => {})
    }, { once: true })
    hdVideoEl.addEventListener('error', () => {
      console.warn('HD segment load error:', hdSource, seg)
      // HEVC playback failed (e.g. Firefox on iOS reports support but can't render)
      // Fall back to qcamera HLS
      onHevcFailed?.()
    }, { once: true })
  }

  function handleHdTimeUpdate() {
    if (!hdVideoEl || !showingHd) return
    const t = hdSegment * 60 + hdVideoEl.currentTime
    currentTime = t
    onTimeUpdate?.(t)
  }

  function handleHdEnded() {
    if (!showingHd) return
    // Check if selectionEnd falls on this segment boundary — let parent loop back
    const segEndTime = (hdSegment + 1) * 60
    if (selectionEnd > 0 && segEndTime >= selectionEnd) {
      // Parent's handleTimeUpdate will seek back to selectionStart
      onTimeUpdate?.(segEndTime)
      return
    }
    // Auto-advance to next segment
    const nextSeg = hdSegment + 1
    if (nextSeg <= maxSegment) {
      loadHdSegment(nextSeg, 0, true)
    } else {
      isPlaying = false
      onPause?.()
    }
  }

  function handleHdPlay() {
    if (!showingHd) return
    if (frozen) { hdVideoEl?.pause(); return }
    isPlaying = true
    onPlay?.()
  }

  function handleHdPause() {
    if (!showingHd) return
    isPlaying = false
    onPause?.()
  }

  // Track previous hdSource to detect transitions (avoids $effect re-trigger on currentTime)
  let prevHdSource = null
  $effect(() => {
    const entering = !!hdSource && !prevHdSource
    const switching = !!hdSource && !!prevHdSource && hdSource !== prevHdSource
    const leaving = !hdSource && !!prevHdSource
    prevHdSource = hdSource

    if ((entering || switching) && hdVideoEl && !showingHud) {
      // Entering or switching HD source — pause everything, load at same position
      videoEl?.pause()
      userWantsPause = true
      isPlaying = false
      onPause?.()
      const seg = Math.floor(currentTime / 60)
      const offset = currentTime % 60
      loadHdSegment(seg, offset)
    } else if (leaving && hdVideoEl) {
      // Leaving HD mode — pause everything, restore HLS at same position
      hdVideoEl.pause()
      hdVideoEl.removeAttribute('src')
      hdVideoEl.load()
      hdSegment = -1
      userWantsPause = true
      isPlaying = false
      onPause?.()
      if (videoEl) {
        videoEl.pause()
        videoEl.currentTime = currentTime
      }
    }
  })

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
    if (frozen) {
      if (videoEl && !videoEl.paused) videoEl.pause()
      if (hdVideoEl && !hdVideoEl.paused) hdVideoEl.pause()
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
    if (showingHd && hdVideoEl) {
      const seg = Math.floor(time / 60)
      const offset = time % 60
      if (seg !== hdSegment) {
        const wasPlaying = !hdVideoEl.paused
        loadHdSegment(seg, offset, wasPlaying)
      } else {
        hdVideoEl.currentTime = offset
      }
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

<div class="relative w-full group bg-black" style="aspect-ratio: {showingHud ? '2160/1080' : '1928/1208'}; contain: strict">
  <!-- HLS video element (qcamera) -->
  <video
    bind:this={videoEl}
    class="absolute inset-0 w-full h-full object-cover"
    class:invisible={showingHd}
    class:hidden={showingHud}
    muted
    playsinline
    webkit-playsinline
    x5-video-player-type="h5"
    preload="auto"
    ontimeupdate={handleHlsTimeUpdate}
    ondurationchange={handleHlsDurationChange}
    onplay={handleHlsPlay}
    onpause={handleHlsPause}
    onended={handleHlsPause}
    onwaiting={() => { if (!showingHud && !showingHd) buffering = true }}
    onplaying={() => { if (!showingHud && !showingHd) buffering = false }}
    oncanplay={() => { if (!showingHud && !showingHd) buffering = false }}
  >
    Your browser does not support video playback.
  </video>

  <!-- HUD live stream video element -->
  <video
    bind:this={hudVideoEl}
    class="absolute inset-0 w-full h-full object-cover"
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
    onwaiting={() => { if (showingHud) buffering = true }}
    onplaying={() => { if (showingHud) buffering = false }}
    oncanplay={() => { if (showingHud) buffering = false }}
  >
  </video>

  <!-- HD camera video element (fcamera/ecamera/dcamera) -->
  <video
    bind:this={hdVideoEl}
    class="absolute inset-0 w-full h-full object-cover"
    class:invisible={!showingHd}
    muted={isMuted}
    playsinline
    webkit-playsinline
    x5-video-player-type="h5"
    preload="auto"
    ontimeupdate={handleHdTimeUpdate}
    onplay={handleHdPlay}
    onpause={handleHdPause}
    onended={handleHdEnded}
    onwaiting={() => { if (showingHd) buffering = true }}
    onplaying={() => { if (showingHd) buffering = false }}
    oncanplay={() => { if (showingHd) buffering = false }}
  >
  </video>

  <!-- Loading indicator when no video loaded -->
  {#if !files?.qcameras?.some(u => u)}
    <div class="absolute inset-0 flex items-center justify-center bg-surface-900">
      <p class="text-surface-400 text-sm">No video available</p>
    </div>
  {:else if buffering}
    <div class="absolute inset-0 z-10 flex items-center justify-center pointer-events-none bg-black/40">
      <div class="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin"></div>
    </div>
  {/if}

  <!-- Hover overlay: HUD stream + download icons -->
  {#if !frozen && !showingHud && (onHudStream || onHudDownload)}
    <div class="absolute bottom-0 left-0 right-0 hidden sm:flex items-center justify-center gap-3 py-2
      bg-gradient-to-t from-black/60 to-transparent
      opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
      {#if onHudStream}
        <button
          class="pointer-events-auto w-8 h-8 flex items-center justify-center rounded-full bg-white/20 hover:bg-white/40 backdrop-blur-sm transition-colors"
          title="HUD Live Stream"
          onclick={onHudStream}
        >
          <!-- Play/monitor icon -->
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <rect x="2" y="3" width="20" height="14" rx="2"/>
            <path d="M8 21h8M12 17v4"/>
            <polygon points="10,7 10,13 15,10" fill="currentColor" stroke="none"/>
          </svg>
        </button>
      {/if}
      {#if onHudDownload}
        <button
          class="pointer-events-auto w-8 h-8 flex items-center justify-center rounded-full bg-white/20 hover:bg-white/40 backdrop-blur-sm transition-colors"
          title="Download HUD Video"
          onclick={onHudDownload}
        >
          <!-- Download icon -->
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
      {/if}
    </div>
  {/if}
</div>
