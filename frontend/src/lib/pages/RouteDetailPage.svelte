<script>
  import { onMount } from 'svelte'
  import { selectedRoute, dongleId } from '../stores.js'
  import { fetchRoute, fetchRouteFiles, fetchAllCoords, fetchAllEventsWithProgress } from '../api.js'
  import { formatDate, formatTime, formatDistance, formatDuration, getRouteDurationMs } from '../format.js'
  import { buildTimelineEvents } from '../derived.js'
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
  let hudEnabled = $state(false)
  let selectionStart = $state(0)
  let selectionEnd = $state(0)
  let enrichDone = $state(0)
  let enrichTotal = $state(0)
  const enriching = $derived(enrichTotal > 0 && enrichDone < enrichTotal)

  let videoPlayer = $state(null)

  const durationMs = $derived(route ? getRouteDurationMs(route) : 0)
  const durationMin = $derived(route?.maxqlog != null ? route.maxqlog + 1 : null)

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
      <span class="text-sm text-surface-200 font-mono truncate">{route.fullname?.split('/')[1] ?? route.fullname}</span>
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
        <VideoPlayer
          bind:this={videoPlayer}
          {route}
          {files}
          {hudEnabled}
          bind:currentTime
          bind:duration
          onTimeUpdate={handleTimeUpdate}
          onPlay={handlePlay}
          onPause={handlePause}
        />

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
            {isPlaying}
            bind:selectionStart
            bind:selectionEnd
          />
        {/if}
      </div>

      <!-- Map + info -->
      <div class="space-y-4">
        {#if !enriching}
          <!-- Map -->
          <RouteMap
            {coords}
            events={timelineEvents}
            {currentTime}
            {durationMs}
            {selectionStart}
            {selectionEnd}
          />

          <!-- Actions -->
          <div class="card p-3 space-y-2">
            <RouteActions {route} />
            <label class="flex items-center gap-2 text-sm text-surface-300 cursor-pointer">
              <input type="checkbox" bind:checked={hudEnabled} class="accent-engage-green" />
              HUD overlay
            </label>
          </div>
        {/if}

        <!-- Metadata -->
        <div class="card p-4 space-y-3">
          <h3 class="text-sm font-semibold text-surface-200">Route Info</h3>

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
        </div>

        <!-- Device Info -->
        <div class="card p-4 space-y-3">
          <h3 class="text-sm font-semibold text-surface-200">Device Info</h3>

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
      </div>
    </div>
  {/if}
</div>
