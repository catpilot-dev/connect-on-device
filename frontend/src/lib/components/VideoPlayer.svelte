<script>
  import { onMount, onDestroy } from 'svelte'
  import Hls from 'hls.js'
  import { hudUrl, spriteUrl } from '../api.js'

  /**
   * Cross-browser HLS video player with HUD overlay.
   *
   * Compatibility strategy:
   * 1. hls.js (Chrome, Firefox, Edge, Opera, Android Chrome/Firefox/Samsung)
   * 2. Native HLS (Safari macOS, Safari iOS, Chrome iOS — all WebKit)
   * 3. Direct .ts fallback (single segment for any remaining edge cases)
   *
   * Mobile considerations:
   * - playsInline: prevents iOS fullscreen takeover
   * - muted: enables autoplay on all mobile browsers (autoplay policy)
   * - preload="auto": hints browser to buffer ahead
   * - webkit-playsinline: legacy iOS support
   * - x5-video-player-type="h5": Tencent WebView (WeChat browser) inline
   * - x5-video-orientation="portraint": WeChat landscape prevention
   */

  /** @type {{ route: object, files: object, hudEnabled?: boolean, currentTime?: number, duration?: number, onTimeUpdate?: (t: number) => void, onDurationChange?: (d: number) => void, onPlay?: () => void, onPause?: () => void }} */
  let {
    route,
    files,
    hudEnabled = false,
    currentTime = $bindable(0),
    duration = $bindable(0),
    onTimeUpdate,
    onDurationChange,
    onPlay,
    onPause,
  } = $props()

  let videoEl = $state(null)
  let hudImg = $state(null)
  let hls = null
  let manifestUrl = null
  let isPlaying = $state(false)
  let hudVisible = $state(false)
  let hudTimer = null
  let lastHudSeg = -1
  let lastHudOffset = -1

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
    for (const url of qcameraUrls) {
      if (!url) {
        lines.push('#EXT-X-GAP', '#EXTINF:60.0,', 'gap')
      } else {
        lines.push(`#EXTINF:60.0,`, url)
      }
    }
    lines.push('#EXT-X-ENDLIST')
    return lines.join('\n')
  }

  function initPlayer() {
    if (!videoEl || !files?.qcameras) return

    cleanup()

    const manifest = buildManifest(files.qcameras)
    const blob = new Blob([manifest], { type: 'application/vnd.apple.mpegurl' })
    manifestUrl = URL.createObjectURL(blob)

    if (Hls.isSupported()) {
      // Path 1: hls.js — Chrome, Firefox, Edge, Opera, Android browsers
      hls = new Hls({
        enableWorker: true,
        // Generous buffering for smooth playback on slower devices
        maxBufferLength: 30,
        maxMaxBufferLength: 120,
        // Start from lowest quality for fast initial load on mobile
        startLevel: 0,
        // Avoid stalling on slow connections
        fragLoadingTimeOut: 20000,
        fragLoadingMaxRetry: 6,
        levelLoadingTimeOut: 10000,
      })
      hls.loadSource(manifestUrl)
      hls.attachMedia(videoEl)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        videoEl.play().catch(() => {})
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
      // Path 2: Native HLS — Safari (macOS + iOS), Chrome iOS, Firefox iOS
      // All iOS browsers use WebKit which has native HLS support
      videoEl.src = manifestUrl
      videoEl.addEventListener('loadedmetadata', () => {
        videoEl.play().catch(() => {})
      }, { once: true })
    } else {
      // Path 3: Direct source fallback — extremely rare edge case
      // Try first available qcamera.ts directly
      const firstUrl = files.qcameras?.find(u => u)
      if (firstUrl) {
        videoEl.src = firstUrl
        videoEl.addEventListener('loadedmetadata', () => {
          videoEl.play().catch(() => {})
        }, { once: true })
      }
    }
  }

  function cleanup() {
    if (hls) {
      hls.destroy()
      hls = null
    }
    if (manifestUrl) {
      URL.revokeObjectURL(manifestUrl)
      manifestUrl = null
    }
    if (hudTimer) {
      clearInterval(hudTimer)
      hudTimer = null
    }
  }

  // Handle video time updates — throttled to avoid excessive events
  function handleTimeUpdate() {
    if (!videoEl) return
    currentTime = videoEl.currentTime
    onTimeUpdate?.(videoEl.currentTime)
  }

  function handleDurationChange() {
    if (!videoEl) return
    duration = videoEl.duration
    onDurationChange?.(videoEl.duration)
  }

  function handlePlay() { isPlaying = true; onPlay?.() }
  function handlePause() { isPlaying = false; onPause?.() }

  // HUD overlay: update every 500ms when playing
  function startHudUpdates() {
    if (hudTimer) clearInterval(hudTimer)
    hudTimer = setInterval(updateHud, 500)
  }

  function stopHudUpdates() {
    if (hudTimer) {
      clearInterval(hudTimer)
      hudTimer = null
    }
  }

  function updateHud() {
    if (!hudImg || !route || !videoEl) return
    const t = videoEl.currentTime
    const seg = Math.floor(t / 60)
    const offsetMs = Math.round((t % 60) * 1000)

    // Skip if same frame
    if (seg === lastHudSeg && Math.abs(offsetMs - lastHudOffset) < 400) return
    lastHudSeg = seg
    lastHudOffset = offsetMs

    hudImg.src = hudUrl(route, seg, offsetMs)
  }

  function handleHudLoad() { hudVisible = true }
  function handleHudError() { hudVisible = false }

  // React to files changing (route switch)
  $effect(() => {
    if (files) initPlayer()
  })

  // React to play/pause and hudEnabled for HUD timer
  $effect(() => {
    if (hudEnabled && isPlaying) {
      startHudUpdates()
      updateHud()
    } else {
      stopHudUpdates()
      if (hudEnabled) updateHud()
    }
    if (!hudEnabled) {
      hudVisible = false
    }
  })

  onDestroy(cleanup)

  // Expose seek method for external control
  export function seek(time) {
    if (videoEl) videoEl.currentTime = time
  }

  export function play() {
    videoEl?.play().catch(() => {})
  }

  export function pause() {
    videoEl?.pause()
  }

  export function toggle() {
    if (!videoEl) return
    videoEl.paused ? videoEl.play().catch(() => {}) : videoEl.pause()
  }

  export function setPlaybackRate(rate) {
    if (videoEl) videoEl.playbackRate = rate
  }
</script>

<div class="relative w-full bg-black rounded-lg overflow-hidden">
  <!-- Video element with maximum compatibility attributes -->
  <video
    bind:this={videoEl}
    class="w-full aspect-video object-contain bg-black"
    muted
    playsinline
    webkit-playsinline
    x5-video-player-type="h5"
    poster={posterUrl}
    preload="auto"
    ontimeupdate={handleTimeUpdate}
    ondurationchange={handleDurationChange}
    onplay={handlePlay}
    onpause={handlePause}
    onended={handlePause}
  >
    Your browser does not support video playback.
  </video>

  <!-- HUD overlay — absolutely positioned over video, only rendered when enabled -->
  {#if hudEnabled}
    <img
      bind:this={hudImg}
      class="absolute inset-0 w-full h-full object-contain pointer-events-none transition-opacity duration-150"
      class:opacity-0={!hudVisible}
      class:opacity-100={hudVisible}
      alt=""
      onload={handleHudLoad}
      onerror={handleHudError}
    />
  {/if}

  <!-- Loading indicator when no video loaded -->
  {#if !files?.qcameras?.some(u => u)}
    <div class="absolute inset-0 flex items-center justify-center bg-surface-900">
      <p class="text-surface-400 text-sm">No video available</p>
    </div>
  {/if}
</div>
