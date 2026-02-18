<script>
  import { onMount } from 'svelte'
  import { selectedRoute, dongleId } from '../stores.js'
  import { fetchRoute, fetchRouteFiles, fetchAllCoords, fetchAllEvents } from '../api.js'
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
      fetchAllEvents(r).then(raw => {
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
    currentTime = t
    isPlaying = true
  }

  function handlePause() {
    isPlaying = false
  }
</script>

<div class="mx-auto max-w-6xl px-4 py-4">
  <!-- Back button -->
  <button class="btn-ghost mb-3 -ml-2 text-sm" onclick={goBack}>
    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M15 19l-7-7 7-7"/>
    </svg>
    Back to routes
  </button>

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
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4" data-video-container>
      <!-- Video player (spans 2 cols on desktop) -->
      <div class="md:col-span-2 space-y-3">
        <VideoPlayer
          bind:this={videoPlayer}
          {route}
          {files}
          bind:currentTime
          bind:duration
          onTimeUpdate={handleTimeUpdate}
        />

        <VideoControls
          {route}
          {currentTime}
          {duration}
          events={timelineEvents}
          {durationMs}
          onSeek={handleSeek}
          onToggle={handleToggle}
          onRate={handleRate}
          {isPlaying}
        />
      </div>

      <!-- Sidebar -->
      <div class="space-y-4">
        <!-- Map -->
        <RouteMap
          {coords}
          events={timelineEvents}
          {currentTime}
          {durationMs}
        />

        <!-- Actions -->
        <div class="card p-3">
          <RouteActions {route} />
        </div>

        <!-- Metadata -->
        <div class="card p-4 space-y-3">
          <h3 class="text-sm font-semibold text-surface-200">Route Info</h3>

          <div class="grid grid-cols-2 gap-2 text-sm">
            <div>
              <p class="text-xs text-surface-500">Date</p>
              <p class="text-surface-200">{formatDate(route.create_time)}</p>
            </div>
            <div>
              <p class="text-xs text-surface-500">Time</p>
              <p class="text-surface-200">{formatTime(route.start_time)}</p>
            </div>
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
            {#if route.version}
              <div>
                <p class="text-xs text-surface-500">Version</p>
                <p class="text-surface-200">{route.version}</p>
              </div>
            {/if}
          </div>

          {#if route.git_branch || route.git_commit}
            <div class="pt-2 border-t border-surface-700/50">
              <p class="text-xs text-surface-500 mb-1">Git</p>
              <p class="text-xs text-surface-300 font-mono truncate">
                {route.git_branch ?? ''}{route.git_commit ? ` @ ${route.git_commit.slice(0, 8)}` : ''}
              </p>
            </div>
          {/if}

          {#if route.start_lat && route.start_lng}
            <div class="pt-2 border-t border-surface-700/50">
              <p class="text-xs text-surface-500 mb-1">GPS Start</p>
              <p class="text-xs text-surface-300 font-mono">
                {route.start_lat.toFixed(5)}, {route.start_lng.toFixed(5)}
              </p>
            </div>
          {/if}

          <div class="pt-2 border-t border-surface-700/50">
            <p class="text-xs text-surface-500 mb-1">Segments</p>
            <p class="text-surface-200 text-sm">{(route.maxqlog ?? 0) + 1}</p>
          </div>
        </div>
      </div>
    </div>
  {/if}
</div>
