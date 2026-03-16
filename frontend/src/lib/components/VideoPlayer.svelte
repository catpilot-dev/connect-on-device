<script>
  import { onMount, onDestroy } from 'svelte'
  import Hls from 'hls.js'
  import { hudUrl, spriteUrl, cameraUrl, hudStreamOffer } from '../api.js'

  /**
   * Cross-browser HLS video player with HUD live stream support.
   *
   * Three video elements:
   * 1. HLS video (qcamera segments) — always loaded, hidden when HUD active
   * 2. HUD live stream video — shown when hudLiveUrl is set
   *    - ws:// or wss:// URL → MSE + WebSocket (fMP4 chunks from h264_v4l2m2m)
   *    - http:// URL → HLS via hls.js (legacy fallback)
   * 3. HD camera video (fcamera/ecamera/dcamera) — shown when hdSource is set
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
  let isPlaying = $state(false)
  let isMuted = $state(true)
  let buffering = $state(true)   // Show spinner until first frame ready
  let userWantsPause = false  // Guard against HLS spurious play events after seek

  // HD (fcamera) state
  let hdSegment = $state(-1)          // currently loaded HD segment

  // MSE/WebSocket state for HUD stream
  let hudWs = null           // WebSocket connection
  let hudMediaSource = null  // MediaSource instance
  let hudSourceBuffer = null // SourceBuffer for H264 fMP4
  let hudMseQueue = []       // Pending chunks while SourceBuffer is updating
  let hudMseReady = false    // SourceBuffer is ready for appends

  // WebRTC state for HUD stream
  let hudPc = null           // RTCPeerConnection

  // Track which video is active for control methods
  const showingHud = $derived(!!hudLiveUrl)
  const showingHd = $derived(!!hdSource && !showingHud)
  const activeVideo = $derived(showingHud ? hudVideoEl : showingHd ? hdVideoEl : videoEl)

  const posterUrl = $derived(route ? spriteUrl(route, 0) : null)

  function initPlayer() {
    if (!videoEl || !files?.qcameras) return

    buffering = true
    cleanupHls()

    // Server-generated HLS manifest with ~4s segments for smooth playback
    const routeName = route.local_id || route.fullname
    const hlsManifestUrl = `/v1/route/${routeName}/qcamera.m3u8`

    if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS (Safari/iOS): always prefer over hls.js — smoother on mobile
      videoEl.src = hlsManifestUrl
      videoEl.addEventListener('loadedmetadata', () => {
        if (!showingHud && !frozen) videoEl.play().catch(() => {})
      }, { once: true })
    } else if (Hls.isSupported()) {
      // hls.js fallback (Firefox/Chrome desktop without native HLS)
      hls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        progressive: true,
        startLevel: 0,
        maxBufferLength: 90,
        maxMaxBufferLength: 300,
        backBufferLength: 60,
        maxBufferHole: 0.1,
        nudgeOffset: 0.05,
        nudgeMaxRetry: 10,
        startFragPrefetch: true,
        highBufferWatchdogPeriod: 1,
        abrEwmaDefaultEstimate: 10_000_000,
        testBandwidth: false,
        forceKeyFrameOnDiscontinuity: false,
        fragLoadingTimeOut: 30000,
        fragLoadingMaxRetry: 5,
        levelLoadingTimeOut: 10000,
      })
      hls.loadSource(hlsManifestUrl)
      hls.attachMedia(videoEl)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (!showingHud && !frozen) videoEl.play().catch(() => {})
      })

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            hls.recoverMediaError()
          } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            hls.startLoad()
          }
        }
      })
    }
  }

  function cleanupHls() {
    if (hls) {
      hls.destroy()
      hls = null
    }
  }

  function cleanupHudHls() {
    if (hudHls) {
      hudHls.destroy()
      hudHls = null
    }
  }

  function cleanupHudMse() {
    hudMseReady = false
    hudMseQueue = []
    if (hudWs) {
      try { hudWs.close() } catch {}
      hudWs = null
    }
    if (hudSourceBuffer && hudMediaSource) {
      try {
        if (hudMediaSource.readyState === 'open') {
          hudSourceBuffer.abort()
        }
      } catch {}
      hudSourceBuffer = null
    }
    if (hudMediaSource) {
      try {
        if (hudMediaSource.readyState === 'open') {
          hudMediaSource.endOfStream()
        }
      } catch {}
      hudMediaSource = null
    }
  }

  function initHudMse(wsUrl) {
    if (!hudVideoEl || !('MediaSource' in window)) return false

    cleanupHudMse()
    cleanupHudHls()

    hudMediaSource = new MediaSource()
    hudVideoEl.src = URL.createObjectURL(hudMediaSource)

    hudMediaSource.addEventListener('sourceopen', () => {
      // H264 High Profile Level 4.1 — matches common hardware encoder output
      const mimeCodec = 'video/mp4; codecs="avc1.640029"'
      if (!MediaSource.isTypeSupported(mimeCodec)) {
        console.error('MSE codec not supported:', mimeCodec)
        return
      }
      hudSourceBuffer = hudMediaSource.addSourceBuffer(mimeCodec)
      hudSourceBuffer.mode = 'sequence'
      hudSourceBuffer.addEventListener('updateend', flushMseQueue)
      hudMseReady = true

      // Open WebSocket to receive fMP4 chunks
      hudWs = new WebSocket(wsUrl)
      hudWs.binaryType = 'arraybuffer'
      hudWs.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
          appendToSourceBuffer(new Uint8Array(ev.data))
        }
      }
      hudWs.onclose = () => {
        // Stream ended — end of stream if MSE still open
        if (hudMediaSource && hudMediaSource.readyState === 'open') {
          try {
            // Wait for pending updates to finish before endOfStream
            if (hudSourceBuffer && !hudSourceBuffer.updating && hudMseQueue.length === 0) {
              hudMediaSource.endOfStream()
            }
          } catch {}
        }
      }
      hudWs.onerror = () => {
        console.error('HUD WebSocket error')
      }

      hudVideoEl.play().catch(() => {})
    })

    return true
  }

  function appendToSourceBuffer(data) {
    if (!hudSourceBuffer || !hudMseReady) return
    if (hudSourceBuffer.updating || hudMseQueue.length > 0) {
      hudMseQueue.push(data)
    } else {
      try {
        hudSourceBuffer.appendBuffer(data)
      } catch (e) {
        console.error('SourceBuffer append error:', e)
        // QuotaExceededError — evict old data
        if (e.name === 'QuotaExceededError' && hudSourceBuffer.buffered.length > 0) {
          const start = hudSourceBuffer.buffered.start(0)
          const end = hudSourceBuffer.buffered.end(0) - 5
          if (end > start) {
            try { hudSourceBuffer.remove(start, end) } catch {}
          }
        }
      }
    }
  }

  function flushMseQueue() {
    if (!hudSourceBuffer || hudSourceBuffer.updating || hudMseQueue.length === 0) return
    const chunk = hudMseQueue.shift()
    try {
      hudSourceBuffer.appendBuffer(chunk)
    } catch (e) {
      console.error('SourceBuffer flush error:', e)
    }
  }

  async function initHudWebRTC() {
    if (!hudVideoEl) return false

    cleanupHudWebRTC()
    cleanupHudMse()
    cleanupHudHls()

    const pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    })
    hudPc = pc

    pc.onconnectionstatechange = () => {
      console.log('[WebRTC] connection state:', pc.connectionState)
    }

    // Receive-only video transceiver
    pc.addTransceiver('video', { direction: 'recvonly' })

    pc.ontrack = (ev) => {
      console.log('[WebRTC] ontrack:', ev.track.kind, 'streams:', ev.streams.length)
      // aiortc may not associate tracks with streams, so create one from the track
      const stream = ev.streams[0] || new MediaStream([ev.track])
      hudVideoEl.srcObject = stream
      hudVideoEl.play().catch((e) => console.error('[WebRTC] play error:', e))
    }

    // Create offer and wait for ICE gathering (LAN = instant)
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    // Wait for ICE gathering to complete (timeout after 3s for LAN)
    await new Promise((resolve) => {
      if (pc.iceGatheringState === 'complete') { resolve(); return }
      const timer = setTimeout(resolve, 3000)
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === 'complete') { clearTimeout(timer); resolve() }
      }
    })

    // Exchange SDP with server
    console.log('[WebRTC] sending offer...')
    const answer = await hudStreamOffer(pc.localDescription.sdp)
    console.log('[WebRTC] got answer, setting remote description')
    await pc.setRemoteDescription(new RTCSessionDescription(answer))

    return true
  }

  function cleanupHudWebRTC() {
    if (hudPc) {
      try { hudPc.close() } catch {}
      hudPc = null
    }
  }

  function cleanup() {
    cleanupHls()
    cleanupHudHls()
    cleanupHudMse()
    cleanupHudWebRTC()
  }

  // ── HLS video event handlers ──────────────────────────────
  function handleSdTimeUpdate() {
    if (!videoEl || showingHud || showingHd) return
    currentTime = videoEl.currentTime
    onTimeUpdate?.(videoEl.currentTime)
  }

  function handleSdDurationChange() {
    if (!videoEl || showingHud) return
    duration = videoEl.duration
    onDurationChange?.(videoEl.duration)
  }

  function handleSdPlay() {
    if (showingHud || showingHd) return
    if (frozen) { videoEl?.pause(); return }
    if (userWantsPause) { videoEl?.pause(); return }
    isPlaying = true
    onPlay?.()
  }

  function handleSdPause() {
    if (showingHud || showingHd) return
    isPlaying = false
    onPause?.()
  }

  // ── HUD live stream event handlers ───────────────────────
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

  // ── HUD live stream: react to hudLiveUrl changes ───────────
  $effect(() => {
    if (hudLiveUrl && hudVideoEl) {
      // Switching TO HUD live stream
      videoEl?.pause()

      const isWebRTC = hudLiveUrl === 'webrtc:'
      const isWs = hudLiveUrl.startsWith('ws:') || hudLiveUrl.startsWith('wss:')

      if (isWebRTC) {
        // WebRTC mode — aiortc encodes H.264, native <video> playback
        initHudWebRTC().catch((e) => console.error('WebRTC init failed:', e))
      } else if (isWs) {
        // WebSocket + MSE mode (fMP4 from hardware encoder)
        cleanupHudHls()
        initHudMse(hudLiveUrl)
      } else if (Hls.isSupported()) {
        // HLS fallback mode
        cleanupHudMse()
        cleanupHudHls()

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
        hudVideoEl.src = hudLiveUrl
        hudVideoEl.addEventListener('loadedmetadata', () => {
          hudVideoEl.play().catch(() => {})
        }, { once: true })
      }
    } else if (!hudLiveUrl && videoEl) {
      // Switching FROM HUD back to qcamera HLS
      cleanupHudWebRTC()
      cleanupHudMse()
      cleanupHudHls()
      if (hudVideoEl) {
        hudVideoEl.pause()
        hudVideoEl.removeAttribute('src')
        hudVideoEl.load()
      }
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
      userWantsPause = false
      const wasPlaying = !videoEl.paused
      videoEl.currentTime = time
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
    disablepictureinpicture
    webkit-playsinline
    x5-video-player-type="h5"
    preload="auto"
    ontimeupdate={handleSdTimeUpdate}
    ondurationchange={handleSdDurationChange}
    onplay={handleSdPlay}
    onpause={handleSdPause}
    onended={handleSdPause}
    onwaiting={() => { if (!showingHud && !showingHd) buffering = true }}
    onplaying={() => { if (!showingHud && !showingHd) buffering = false }}
    oncanplay={() => { if (!showingHud && !showingHd) buffering = false }}
  >
    Your browser does not support video playback.
  </video>

  <!-- HD camera video element (fcamera/ecamera/dcamera) -->
  <video
    bind:this={hdVideoEl}
    class="absolute inset-0 w-full h-full object-cover"
    class:invisible={!showingHd}
    muted={isMuted}
    playsinline
    disablepictureinpicture
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

  <!-- HUD live stream video element -->
  <video
    bind:this={hudVideoEl}
    class="absolute inset-0 w-full h-full object-cover"
    class:hidden={!showingHud}
    muted
    playsinline
    disablepictureinpicture
    webkit-playsinline
    x5-video-player-type="h5"
    preload="auto"
    onplay={handleHudPlay}
    onpause={handleHudPause}
    onended={handleHudPause}
    onwaiting={() => { if (showingHud) buffering = true }}
    onplaying={() => { if (showingHud) buffering = false }}
    oncanplay={() => { if (showingHud) buffering = false }}
  >
  </video>

  <!-- Loading indicator when no video loaded -->
  {#if !files?.qcameras?.some(u => u)}
    <div class="absolute inset-0 flex items-center justify-center bg-surface-900">
      <p class="text-surface-400 text-sm">No video available</p>
    </div>
  {:else if buffering && !showingHud}
    <div class="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
      <div class="w-8 h-8 border-3 border-white/30 border-t-white rounded-full animate-spin drop-shadow-lg"></div>
    </div>
  {/if}

  <!-- Hover overlay: HUD stream + download buttons (centered) -->
  {#if !frozen && !showingHud && (onHudStream || onHudDownload)}
    <div class="absolute inset-0 hidden sm:flex items-center justify-center gap-4
      opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
      {#if onHudStream}
        <button
          class="pointer-events-auto flex items-center gap-2 px-4 py-2.5 rounded-lg bg-black/50 hover:bg-black/70 border border-transparent hover:border-blue-500 backdrop-blur-sm transition-colors"
          title="HUD UI Preview"
          onclick={onHudStream}
        >
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <rect x="2" y="3" width="20" height="14" rx="2"/>
            <path d="M8 21h8M12 17v4"/>
            <polygon points="10,7 10,13 15,10" fill="currentColor" stroke="none"/>
          </svg>
          <span class="text-white text-sm font-medium">HUD Preview</span>
        </button>
      {/if}
      {#if onHudDownload}
        <button
          class="pointer-events-auto flex items-center gap-2 px-4 py-2.5 rounded-lg bg-black/50 hover:bg-black/70 border border-transparent hover:border-blue-500 backdrop-blur-sm transition-colors"
          title="Download HUD Video"
          onclick={onHudDownload}
        >
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          <span class="text-white text-sm font-medium">HUD Download</span>
        </button>
      {/if}
    </div>
  {/if}
</div>
