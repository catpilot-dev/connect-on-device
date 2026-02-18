<script>
  import { selectedRoute } from '../stores.js'
  import { fetchAllEvents } from '../api.js'
  import { formatDate, formatTime, formatDuration, formatDistance, getRouteDurationMs } from '../format.js'
  import { buildTimelineEvents, calcRouteStats } from '../derived.js'
  import Filmstrip from './Filmstrip.svelte'
  import EventTimeline from './EventTimeline.svelte'

  /** @type {{ route: object }} */
  let { route } = $props()

  let timelineEvents = $state([])
  let engagedPct = $state(0)

  const durationMs = $derived(getRouteDurationMs(route))
  const durationMin = $derived(
    route.maxqlog != null ? route.maxqlog + 1 : null
  )

  // Fetch events for timeline
  $effect(() => {
    fetchAllEvents(route).then((rawEvents) => {
      const evts = buildTimelineEvents(rawEvents, durationMs)
      timelineEvents = evts
      const stats = calcRouteStats(evts)
      engagedPct = durationMs > 0 ? Math.round((stats.engagedMs / durationMs) * 100) : 0
    }).catch(() => {})
  })

  function handleClick() {
    selectedRoute.set(route.fullname)
  }

  function handleFilmstripClick(seg) {
    selectedRoute.set(route.fullname)
  }
</script>

<button
  class="card w-full text-left cursor-pointer hover:border-surface-500/50 transition-colors duration-150 overflow-hidden"
  onclick={handleClick}
>
  <!-- Filmstrip -->
  <div class="relative">
    <Filmstrip {route} maxSegment={route.maxqlog ?? 0} onclick={handleFilmstripClick} />
    <!-- Timeline bar overlaid at bottom of filmstrip -->
    <div class="absolute bottom-0 left-0 right-0 px-1 pb-0.5">
      <EventTimeline events={timelineEvents} {durationMs} />
    </div>
  </div>

  <!-- Metadata row -->
  <div class="px-3 py-2.5 flex items-center gap-3 flex-wrap text-sm">
    <div class="flex items-center gap-2 min-w-0">
      <span class="text-surface-100 font-medium truncate">
        {formatDate(route.create_time)}
      </span>
      <span class="text-surface-400">{formatTime(route.start_time)}</span>
    </div>

    <div class="flex items-center gap-2 text-xs text-surface-400 ml-auto">
      {#if durationMin != null}
        <span>{formatDuration(durationMin)}</span>
      {/if}
      {#if route.distance != null}
        <span class="text-surface-600">&middot;</span>
        <span>{formatDistance(route.distance)}</span>
      {/if}
      {#if engagedPct > 0}
        <span class="text-surface-600">&middot;</span>
        <span class="text-engage-green">{engagedPct}%</span>
      {/if}
      {#if route.platform}
        <span class="text-surface-600">&middot;</span>
        <span class="badge bg-surface-700 text-surface-300">{route.platform}</span>
      {/if}
      {#if route.is_preserved}
        <span title="Preserved" class="text-engage-blue">&#9733;</span>
      {/if}
    </div>
  </div>
</button>
