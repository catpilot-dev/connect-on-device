<script>
  import { onMount, onDestroy } from 'svelte'
  import { selectedRoute, dongleId } from '../stores.js'
  import { fetchRoute, fetchRouteFiles, fetchAllCoords, fetchAllEventsWithProgress, enrichRoute, startHudStream, stopHudStream, hudStreamStatus, hudStreamUrl, prerenderHud, hudProgress, hudVideoUrl, cancelHudRender, saveNote, takeScreenshot } from '../api.js'
  import { formatDate, formatTime, formatDistance, formatDuration, getRouteDurationMs } from '../format.js'
  import { buildTimelineEvents } from '../derived.js'
  import snarkdown from 'snarkdown'
  import VideoPlayer from '../components/VideoPlayer.svelte'
  import VideoControls from '../components/VideoControls.svelte'
  import RouteMap from '../components/RouteMap.svelte'
  import RouteActions from '../components/RouteActions.svelte'

  let route = $state(null)
  let files = $state(null)
  let coords = $state([])
  let timelineEvents = $state([])
  let loading = $state(true)
  let error = $state(null)

  let currentTime = $state(0)
  let duration = $state(0)
  let isPlaying = $state(false)
  let hudWanted = $state(false)
  let hudStarting = $state(false)
  let hudStreaming = $state(false)
  let hudError = $state(null)
  let hudPollTimer = null
  let hudTickTimer = null
  let hudStartTime = 0  // currentTime when stream was started
  const hudLiveUrl = $derived(hudStreaming ? hudStreamUrl() : null)
  // HUD download (pre-render to MP4)
  let dlRendering = $state(false)
  let dlReady = $state(false)
  let dlError = $state(null)
  let dlElapsed = $state(0)
  let dlTotal = $state(0)
  let dlPhase = $state('')
  let dlFrame = $state(0)
  let dlTotalFrames = $state(0)
  let dlRemainingSec = $state(0)
  let dlRenderStart = $state(0)
  let dlPollTimer = null

  // Quality slider: controls resolution continuously (0-100)
  // Single-pass render at 0.2x replay speed → 25fps unique route content from 5fps capture
  let dlQualityPct = $state(50)
  const DL_RECORD_SPEED = 0.2
  const DL_FPS = 20
  // Resolution: slider interpolates width from 540 to 2160 (height is half)
  // Round to even — libx264 requires dimensions divisible by 2
  const dlWidth = $derived(Math.round((540 + (dlQualityPct / 100) * 1620) / 2) * 2)   // 540 → 2160
  const dlHeight = $derived(Math.round((dlWidth / 2) / 2) * 2)                         // 270 → 1080
  // Use scale filter when below native (2160x1080)
  const dlScale = $derived(dlQualityPct < 100 ? `${dlWidth}:${dlHeight}` : null)
  // Bitrate scales roughly with pixel count (quadratic with width)
  const dlBitrate = $derived(0.4 + 2.6 * Math.pow(dlQualityPct / 100, 2))   // ~0.4 → 3.0 Mbps
  const dlDurationSec = $derived(
    (selectionEnd > 0 ? selectionEnd : duration || ((route?.maxqlog ?? 0) + 1) * 60) - (selectionStart || 0)
  )
  const dlEstimatedMB = $derived(Math.round(dlDurationSec * dlBitrate / 8))
  // Single-pass render at 0.2x speed + setup overhead
  const dlEstimatedRenderSec = $derived(Math.round(dlDurationSec / DL_RECORD_SPEED + 30))

  let screenshotBusy = $state(false)

  let noteText = $state('')
  let editingNote = $state(false)

  let selectionStart = $state(0)
  let selectionEnd = $state(0)
  let enrichDone = $state(0)
  let enrichTotal = $state(0)
  const enriching = $derived(enrichTotal > 0 && enrichDone < enrichTotal)

  let videoPlayer = $state(null)

  const durationMs = $derived(route ? getRouteDurationMs(route) : 0)
  const durationMin = $derived(route?.maxqlog != null ? route.maxqlog + 1 : null)
  const bookmarks = $derived(timelineEvents.filter(e => e.type === 'user_flag').map(e => e.route_offset_millis))

  onMount(async () => {
    const name = $selectedRoute
    if (!name) return

    try {
      const [r, f] = await Promise.all([
        fetchRoute(name),
        fetchRouteFiles(name),
      ])
      route = r
      files = f
      noteText = r.notes || ''

      // Fetch coords and events in background (non-blocking)
      fetchAllCoords(r).then(c => coords = c).catch(() => {})
      enrichTotal = (r.maxqlog ?? 0) + 1
      fetchAllEventsWithProgress(r, (done, total) => {
        enrichDone = done
        enrichTotal = total
      }).then(raw => {
        timelineEvents = buildTimelineEvents(raw, getRouteDurationMs(r))
      }).catch(() => {})
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  })

  onDestroy(() => {
    // Stop HUD stream if active when navigating away
    if (hudPollTimer) { clearInterval(hudPollTimer); hudPollTimer = null }
    if (dlPollTimer) { clearInterval(dlPollTimer); dlPollTimer = null }
    stopHudTick()
    if (hudWanted) stopHudStream().catch(() => {})
  })

  function goBack() {
    selectedRoute.set(null)
  }

  function handleSeek(t) {
    currentTime = t
    videoPlayer?.seek(t)
  }

  function handleToggle() {
    videoPlayer?.toggle()
  }

  function handleRate(r) {
    videoPlayer?.setPlaybackRate(r)
  }

  function handleTimeUpdate(t) {
    // Loop within selection range
    if (selectionEnd > 0 && t >= selectionEnd) {
      videoPlayer?.seek(selectionStart)
      currentTime = selectionStart
      return
    }
    currentTime = t
  }

  function handlePlay() {
    isPlaying = true
  }

  function handlePause() {
    isPlaying = false
  }

  async function handleScreenshot() {
    if (!route || screenshotBusy) return
    screenshotBusy = true
    try {
      const res = await takeScreenshot(route.fullname, currentTime)
      const blob = await res.blob()
      // Extract filename from Content-Disposition header, or build fallback
      const cd = res.headers.get('Content-Disposition') || ''
      const match = cd.match(/filename="?([^"]+)"?/)
      const filename = match ? match[1] : 'screenshot.jpg'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      console.error('Screenshot failed:', e)
    } finally {
      screenshotBusy = false
    }
  }

  function stopHudTick() {
    if (hudTickTimer) { clearInterval(hudTickTimer); hudTickTimer = null }
  }

  function startHudTick() {
    stopHudTick()
    hudStartTime = currentTime
    // Advance playhead at 1s/s — replay runs at real-time
    hudTickTimer = setInterval(() => {
      currentTime += 1
      // Respect selection range (selectionEnd defaults to duration)
      if (selectionEnd > 0 && currentTime >= selectionEnd) {
        currentTime = selectionStart
        hudStartTime = selectionStart
      } else if (duration > 0 && currentTime >= duration) {
        currentTime = 0
        hudStartTime = 0
      }
    }, 1000)
  }

  function restoreVideoDefaults() {
    // Restore normal video playback after HUD stream or download finishes
    videoPlayer?.setPlaybackRate(1.0)
    videoPlayer?.pause()
    isPlaying = false
  }

  async function toggleHud() {
    if (hudWanted) {
      // Turning off — stop stream and restore normal video
      hudWanted = false
      if (hudPollTimer) { clearInterval(hudPollTimer); hudPollTimer = null }
      stopHudTick()
      hudStarting = false
      hudStreaming = false
      hudError = null
      stopHudStream().catch(() => {})
      restoreVideoDefaults()
      return
    }
    hudWanted = true
    hudStarting = true
    hudStreaming = false
    hudError = null
    try {
      await startHudStream(route.fullname, currentTime)
      // Poll for streaming status
      hudPollTimer = setInterval(async () => {
        try {
          const s = await hudStreamStatus()
          if (s.status === 'streaming') {
            clearInterval(hudPollTimer)
            hudPollTimer = null
            hudStarting = false
            hudStreaming = true
            startHudTick()
          } else if (s.status === 'error') {
            clearInterval(hudPollTimer)
            hudPollTimer = null
            hudStarting = false
            hudError = s.error || 'Stream failed'
            hudWanted = false
          }
        } catch { /* ignore poll errors */ }
      }, 2000)
    } catch (e) {
      hudStarting = false
      hudError = e.message
      hudWanted = false
    }
  }

  async function startDownload() {
    if (dlRendering || dlReady) return
    dlRendering = true
    dlReady = false
    dlError = null
    dlElapsed = 0
    dlTotal = 0
    dlPhase = ''
    dlFrame = 0
    dlTotalFrames = 0
    dlRemainingSec = 0
    dlRenderStart = selectionStart || 0

    try {
      const start = selectionStart || 0
      const end = selectionEnd > 0 ? selectionEnd : duration || ((route.maxqlog + 1) * 60)
      const res = await prerenderHud(route.fullname, start, end, {
        scale: dlScale,
        fps: DL_FPS,
      })

      if (res.status === 'complete') {
        dlRendering = false
        dlReady = true
        return
      }

      dlTotal = res.total_sec || (end - start)

      // Seek video to render start (frozen=dlRendering keeps it paused)
      videoPlayer?.seek(start)

      // Poll for progress — seek video preview to follow render position
      dlPollTimer = setInterval(async () => {
        try {
          const p = await hudProgress(route.fullname)
          dlElapsed = p.elapsed_sec || 0
          dlTotal = p.total_sec || dlTotal
          dlPhase = p.phase || ''
          dlFrame = p.frame || 0
          dlTotalFrames = p.total_frames || dlTotalFrames
          dlRemainingSec = p.remaining_sec ?? dlRemainingSec

          // Sync video preview to current render position (frozen prop keeps it paused)
          if (p.elapsed_sec != null && p.status === 'rendering') {
            const renderPos = dlRenderStart + (p.elapsed_sec || 0)
            currentTime = renderPos
            videoPlayer?.seek(renderPos)
          }

          if (p.status === 'complete') {
            clearInterval(dlPollTimer)
            dlPollTimer = null
            dlRendering = false
            dlReady = true
            restoreVideoDefaults()
          } else if (p.status === 'error') {
            clearInterval(dlPollTimer)
            dlPollTimer = null
            dlRendering = false
            dlError = p.error || 'Render failed'
            restoreVideoDefaults()
          }
        } catch { /* ignore poll errors */ }
      }, 2000)
    } catch (e) {
      dlRendering = false
      dlError = e.message
      restoreVideoDefaults()
    }
  }

  async function cancelDownload() {
    if (dlPollTimer) { clearInterval(dlPollTimer); dlPollTimer = null }
    dlRendering = false
    dlReady = false
    dlError = null
    restoreVideoDefaults()
    try {
      if (route) await cancelHudRender(route.fullname)
    } catch { /* ignore */ }
  }

  async function saveNoteHandler() {
    editingNote = false
    if (route) await saveNote(route.fullname, noteText)
  }

  let showMap = $state(false)
  let showHudOverlay = $state(false)
  let showRouteInfo = $state(false)
  let showDeviceInfo = $state(false)

  let reEnriching = $state(false)

  async function reEnrich() {
    if (!route || reEnriching) return
    reEnriching = true
    try {
      // Clear cached derived files on server
      await enrichRoute(route.fullname)
      // Re-fetch events (regenerates events.json from rlogs)
      enrichDone = 0
      enrichTotal = (route.maxqlog ?? 0) + 1
      const raw = await fetchAllEventsWithProgress(route, (done, total) => {
        enrichDone = done
        enrichTotal = total
      })
      timelineEvents = buildTimelineEvents(raw, getRouteDurationMs(route))
      // Re-fetch coords too
      fetchAllCoords(route).then(c => coords = c).catch(() => {})
    } catch (e) {
      console.error('Re-enrich failed:', e)
    } finally {
      reEnriching = false
    }
  }

  function openDownload() {
    if (!route) return
    const a = document.createElement('a')
    a.href = hudVideoUrl(route.fullname)
    a.download = `${route.fullname.replace('/', '_')}_hud.mp4`
    a.click()
  }
</script>

<div class="mx-auto max-w-6xl px-4 py-4">
  <!-- Back button -->
  <div class="flex items-center gap-3 mb-3">
    <button class="btn-ghost -ml-2 text-sm" onclick={goBack}>
      <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M15 19l-7-7 7-7"/>
      </svg>
      Back
    </button>
    {#if route}
      <span class="text-sm text-surface-100 font-medium">{formatDate(route.create_time)}</span>
      <span class="text-xs text-surface-400">{formatTime(route.start_time)}</span>
    {/if}
  </div>

  {#if loading}
    <div class="space-y-4">
      <div class="card animate-pulse aspect-video bg-surface-800 rounded-lg"></div>
      <div class="h-10 bg-surface-800 rounded-lg animate-pulse"></div>
    </div>
  {:else if error}
    <div class="flex items-center justify-center h-48">
      <p class="text-surface-400">{error}</p>
    </div>
  {:else if route}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Video player + controls -->
      <div class="space-y-3" data-video-container>
        <div class="rounded-lg overflow-hidden" class:recording-border={dlRendering}>
        <VideoPlayer
          bind:this={videoPlayer}
          {route}
          {files}
          {hudLiveUrl}
          frozen={dlRendering}
          {selectionStart}
          {selectionEnd}
          bind:currentTime
          bind:duration
          onTimeUpdate={handleTimeUpdate}
          onPlay={handlePlay}
          onPause={handlePause}
        />
        </div>

        {#if enriching}
          <div class="space-y-1">
            <div class="h-2 bg-surface-700 rounded-full overflow-hidden">
              <div
                class="h-full bg-engage-green rounded-full transition-all duration-300"
                style="width: {(enrichDone / enrichTotal) * 100}%"
              ></div>
            </div>
            <p class="text-xs text-surface-400 text-center">Enriching segments {enrichDone}/{enrichTotal}</p>
          </div>
        {:else}
          <VideoControls
            {route}
            {currentTime}
            {duration}
            events={timelineEvents}
            {durationMs}
            startTime={route.start_time}
            onSeek={handleSeek}
            onToggle={handleToggle}
            onRate={handleRate}
            onScreenshot={handleScreenshot}
            {isPlaying}
            {screenshotBusy}
            bind:selectionStart
            bind:selectionEnd
          />
        {/if}

        <!-- Note -->
        <div class="card p-4 space-y-2">
          <h3 class="text-sm font-medium text-surface-200">Note</h3>
          {#if editingNote}
            <!-- svelte-ignore a11y_autofocus -->
            <textarea
              class="w-full bg-surface-700 text-surface-100 rounded p-2 text-sm resize-y min-h-[80px] outline-none focus:ring-1 focus:ring-surface-500"
              bind:value={noteText}
              onblur={saveNoteHandler}
              onkeydown={(e) => { if (e.ctrlKey && e.key === 'Enter') saveNoteHandler() }}
              autofocus
            ></textarea>
          {:else if noteText}
            <button
              type="button"
              class="w-full text-left text-sm cursor-pointer text-surface-200 note-rendered"
              onclick={() => { editingNote = true }}
            >
              {@html snarkdown(noteText)}
            </button>
          {:else}
            <button
              type="button"
              class="w-full text-left text-sm cursor-pointer text-surface-500"
              onclick={() => { editingNote = true }}
            >
              Click to add a note...
            </button>
          {/if}
        </div>

        <!-- Bookmarks -->
        {#if bookmarks.length}
          <div class="card p-4 space-y-2">
            <h3 class="text-sm font-medium text-surface-200">Bookmarks</h3>
            <div class="flex flex-wrap gap-2">
              {#each bookmarks as bm, i}
                {@const totalSec = Math.floor(bm / 1000)}
                {@const mm = Math.floor(totalSec / 60)}
                {@const ss = (totalSec % 60).toString().padStart(2, '0')}
                {@const isActive = currentTime >= bm / 1000 - 2 && (i + 1 >= bookmarks.length || currentTime < bookmarks[i + 1] / 1000 - 2)}
                <button
                  class="px-2 py-1 text-xs font-mono rounded text-surface-200"
                  class:bg-engage-green={isActive}
                  class:text-black={isActive}
                  class:bg-surface-700={!isActive}
                  class:hover:bg-surface-600={!isActive}
                  onclick={() => handleSeek(Math.max(0, bm / 1000 - 2))}
                >
                  {mm}:{ss}
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </div>

      <!-- Map + info -->
      <div class="space-y-4">
        {#if !enriching}
          <!-- Map -->
          <div class="card">
            <button class="w-full flex items-center justify-between p-4" onclick={() => showMap = !showMap}>
              <h3 class="text-sm font-semibold text-surface-200">Map</h3>
              <svg class="w-4 h-4 text-surface-400 transition-transform" class:rotate-180={showMap} fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            {#if showMap}
              <div class="px-4 pb-4">
                <RouteMap
                  {coords}
                  events={timelineEvents}
                  {currentTime}
                  {durationMs}
                  {selectionStart}
                  {selectionEnd}
                />
              </div>
            {/if}
          </div>

          <!-- Openpilot UI Overlay (HUD stream + download) -->
          <div class="card">
            <button class="w-full flex items-center justify-between p-4" onclick={() => showHudOverlay = !showHudOverlay}>
              <h3 class="text-sm font-semibold text-surface-200">Openpilot UI Overlay</h3>
              <svg class="w-4 h-4 text-surface-400 transition-transform" class:rotate-180={showHudOverlay} fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            {#if showHudOverlay}
              <div class="px-4 pb-4 space-y-4">
                <!-- HUD Live Stream -->
                <div class="space-y-2" class:opacity-50={dlRendering}>
                  <label class="flex items-center gap-2 text-sm text-surface-200" class:cursor-pointer={!dlRendering} class:cursor-not-allowed={dlRendering}>
                    <input type="checkbox" checked={hudWanted} onchange={toggleHud} disabled={dlRendering} class="accent-engage-green" />
                    HUD Live Stream
                  </label>
                  {#if dlRendering}
                    <p class="text-xs text-yellow-400">Unavailable while rendering video</p>
                  {/if}
                  {#if hudStarting}
                    <div class="flex items-center gap-2">
                      <div class="w-3 h-3 border-2 border-engage-green border-t-transparent rounded-full animate-spin"></div>
                      <p class="text-xs text-surface-400">Starting stream...</p>
                    </div>
                  {/if}
                  {#if hudStreaming}
                    <div class="flex items-center gap-2">
                      <div class="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
                      <p class="text-xs text-surface-400">LIVE</p>
                    </div>
                  {/if}
                  {#if hudError}
                    <p class="text-xs text-red-400">{hudError}</p>
                  {/if}
                </div>

                <div class="border-t border-surface-700/50"></div>

                <!-- Download HUD Video -->
                <div class="space-y-2" class:opacity-50={hudWanted}>
                  <p class="text-sm text-surface-200">Download HUD Video</p>
                  {#if hudWanted}
                    <p class="text-xs text-surface-500">Stop live stream to render video</p>
                  {:else}
                    <p class="text-xs text-surface-400">Render openpilot UI overlay via slow-motion replay{selectionEnd > 0 ? ' (selection range)' : ''}</p>

                    {#if !dlRendering && !dlReady}
                      <!-- Quality slider (continuous) -->
                      <div class="flex items-center gap-3">
                        <span class="text-xs text-surface-500 shrink-0">Low</span>
                        <input type="range" min="0" max="100" step="1" bind:value={dlQualityPct}
                          class="flex-1 accent-engage-green h-1.5" />
                        <span class="text-xs text-surface-500 shrink-0">High</span>
                      </div>
                      <p class="text-xs text-surface-300">{dlWidth}x{dlHeight} @ 20fps</p>
                      <p class="text-xs text-surface-400">
                        ~{dlEstimatedMB} MB
                        · ~{Math.ceil(dlEstimatedRenderSec / 60)} min render time
                      </p>
                      <button class="btn btn-sm bg-surface-700 hover:bg-surface-600 text-surface-200 px-3 py-1 rounded text-xs" onclick={startDownload}>
                        Render & Download
                      </button>
                    {:else if dlRendering}
                      <div class="space-y-1">
                        <div class="h-2 bg-surface-700 rounded-full overflow-hidden">
                          <div
                            class="h-full bg-engage-green rounded-full transition-all duration-300"
                            style="width: {dlTotal > 0 ? (dlElapsed / dlTotal) * 100 : 0}%"
                          ></div>
                        </div>
                        <p class="text-xs text-surface-400 text-center">
                          {dlPhase === 'encoding' ? 'Encoding' : 'Rendering'}... {dlFrame} / {dlTotalFrames} frames
                          {#if dlRemainingSec > 60}
                            · ~{Math.ceil(dlRemainingSec / 60)} min remaining
                          {:else if dlRemainingSec > 0}
                            · ~{dlRemainingSec}s remaining
                          {/if}
                        </p>
                        <button class="btn btn-sm bg-surface-700 hover:bg-red-900 text-surface-300 px-3 py-1 rounded text-xs" onclick={cancelDownload}>
                          Stop
                        </button>
                      </div>
                    {:else if dlReady}
                      <button class="btn btn-sm bg-engage-green text-black font-medium px-3 py-1 rounded" onclick={openDownload}>
                        Download MP4
                      </button>
                      <button class="btn btn-sm bg-surface-700 hover:bg-surface-600 text-surface-200 px-3 py-1 rounded text-xs ml-2"
                        onclick={() => { dlReady = false; dlRendering = false }}>
                        Re-render
                      </button>
                    {/if}
                    {#if dlError}
                      <p class="text-xs text-red-400">{dlError}</p>
                    {/if}
                  {/if}
                </div>
              </div>
            {/if}
          </div>
        {/if}

        <!-- Metadata -->
        <div class="card">
          <button class="w-full flex items-center justify-between p-4" onclick={() => showRouteInfo = !showRouteInfo}>
            <h3 class="text-sm font-semibold text-surface-200">Route Info</h3>
            <svg class="w-4 h-4 text-surface-400 transition-transform" class:rotate-180={showRouteInfo} fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
              <path d="M19 9l-7 7-7-7"/>
            </svg>
          </button>
          {#if showRouteInfo}
            <div class="px-4 pb-4 space-y-3">
              <div class="grid grid-cols-2 gap-2 text-sm">
                {#if route.local_id}
                  <div class="col-span-2">
                    <p class="text-xs text-surface-500">Disk ID</p>
                    <p class="text-surface-200 font-mono truncate">{route.local_id}</p>
                  </div>
                {/if}
                {#if route.start_address}
                  <div>
                    <p class="text-xs text-surface-500">Start</p>
                    <p class="text-surface-200 truncate">{route.start_address}</p>
                  </div>
                  {#if route.end_address && route.end_address !== route.start_address}
                    <div>
                      <p class="text-xs text-surface-500">End</p>
                      <p class="text-surface-200 truncate">{route.end_address}</p>
                    </div>
                  {/if}
                {/if}
                <div>
                  <p class="text-xs text-surface-500">Duration</p>
                  <p class="text-surface-200">{durationMin != null ? formatDuration(durationMin) : '--'}</p>
                </div>
                <div>
                  <p class="text-xs text-surface-500">Distance</p>
                  <p class="text-surface-200">{formatDistance(route.distance)}</p>
                </div>
                {#if route.platform}
                  <div>
                    <p class="text-xs text-surface-500">Car</p>
                    <p class="text-surface-200">{route.platform}</p>
                  </div>
                {/if}
                <div>
                  <p class="text-xs text-surface-500">Segments</p>
                  <p class="text-surface-200">{(route.maxqlog ?? 0) + 1}</p>
                </div>
              </div>
              <button
                class="btn btn-sm bg-surface-700 hover:bg-surface-600 text-surface-200 px-3 py-1 rounded text-xs disabled:opacity-50"
                onclick={reEnrich}
                disabled={reEnriching || enriching}
              >
                {reEnriching ? 'Re-enriching...' : 'Re-enrich'}
              </button>

              <div class="border-t border-surface-700/50 pt-3">
                <RouteActions {route} />
              </div>
            </div>
          {/if}
        </div>

        <!-- Device Info -->
        <div class="card">
          <button class="w-full flex items-center justify-between p-4" onclick={() => showDeviceInfo = !showDeviceInfo}>
            <h3 class="text-sm font-semibold text-surface-200">Device Info</h3>
            <svg class="w-4 h-4 text-surface-400 transition-transform" class:rotate-180={showDeviceInfo} fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
              <path d="M19 9l-7 7-7-7"/>
            </svg>
          </button>
          {#if showDeviceInfo}
            <div class="px-4 pb-4 space-y-3">
              <div class="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <p class="text-xs text-surface-500">Device</p>
                  <p class="text-surface-200">{route.device_type === 'tici' ? 'Comma 3' : route.device_type === 'tizi' ? 'Comma 3X' : route.device_type === 'mici' ? 'Comma 4' : route.device_type ?? '--'}</p>
                </div>
                {#if route.agnos_version}
                  <div>
                    <p class="text-xs text-surface-500">AGNOS</p>
                    <p class="text-surface-200">{route.agnos_version}</p>
                  </div>
                {/if}
                {#if route.version}
                  <div>
                    <p class="text-xs text-surface-500">Openpilot</p>
                    <p class="text-surface-200">{route.version}</p>
                  </div>
                {/if}
                {#if route.git_branch}
                  <div>
                    <p class="text-xs text-surface-500">Branch</p>
                    <p class="text-surface-200 truncate">{route.git_branch}</p>
                  </div>
                {/if}
              </div>

              {#if route.git_commit && route.git_remote}
                {@const repoUrl = route.git_remote.replace(/\.git$/, '')}
                <div class="pt-2 border-t border-surface-700/50">
                  <p class="text-xs text-surface-500 mb-1">Commit</p>
                  <a
                    href="{repoUrl}/commit/{route.git_commit}"
                    target="_blank"
                    rel="noopener"
                    class="text-xs text-engage-blue font-mono hover:underline truncate block"
                  >{route.git_commit.slice(0, 12)}</a>
                </div>
              {:else if route.git_commit}
                <div class="pt-2 border-t border-surface-700/50">
                  <p class="text-xs text-surface-500 mb-1">Commit</p>
                  <p class="text-xs text-surface-300 font-mono truncate">{route.git_commit.slice(0, 12)}</p>
                </div>
              {/if}
            </div>
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .recording-border {
    border: 2px solid transparent;
    animation: recording-blink 1s ease-in-out infinite;
  }
  @keyframes recording-blink {
    0%, 100% { border-color: transparent; }
    50% { border-color: #ef4444; }
  }
  .note-rendered :global(h1) { font-size: 1.25em; font-weight: 600; margin: 0.4em 0 0.2em; }
  .note-rendered :global(h2) { font-size: 1.1em; font-weight: 600; margin: 0.4em 0 0.2em; }
  .note-rendered :global(h3) { font-size: 1em; font-weight: 600; margin: 0.3em 0 0.2em; }
  .note-rendered :global(p) { margin: 0.25em 0; }
  .note-rendered :global(a) { color: #58a6ff; text-decoration: underline; }
  .note-rendered :global(code) { background: rgba(255,255,255,0.08); padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.85em; }
  .note-rendered :global(strong) { color: #e2e8f0; }
  .note-rendered :global(ul) { padding-left: 1.2em; margin: 0.25em 0; list-style: disc; }
  .note-rendered :global(ol) { padding-left: 1.2em; margin: 0.25em 0; list-style: decimal; }
  .note-rendered :global(li) { margin: 0.1em 0; }
</style>
