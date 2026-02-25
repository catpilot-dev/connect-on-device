<script>
  import { selectedRoute } from '../stores.js'
  import { formatDate, formatTimeRange, formatDuration, formatDistance, getRouteDurationMs } from '../format.js'
  import Filmstrip from './Filmstrip.svelte'
  import EventTimeline from './EventTimeline.svelte'

  /** @type {{ route: object }} */
  let { route } = $props()

  const durationMin = $derived(
    route.maxqlog != null ? route.maxqlog + 1 : null
  )
  const durationMs = $derived(getRouteDurationMs(route))

  const hasEnrichedData = $derived(route.engagement_pct != null)
  const timelineEvents = $derived(route.timeline ?? [])

  function handleClick() {
    selectedRoute.set(route.local_id)
  }
</script>

<button
  class="card w-full text-left cursor-pointer hover:border-surface-500/50 transition-colors duration-150 overflow-hidden"
  onclick={handleClick}
>
  <!-- Filmstrip -->
  <div class="relative">
    <Filmstrip {route} maxSegment={route.maxqlog ?? 0} onclick={() => handleClick()} />
  </div>

  <!-- Event timeline (only if enriched) -->
  {#if hasEnrichedData}
    <div class="px-3 pt-2">
      <EventTimeline events={timelineEvents} {durationMs} height="4px" />
    </div>
  {/if}

  <!-- Address row -->
  {#if route.start_address}
    <div class="px-3 pt-2 text-xs text-surface-300 truncate">
      {route.start_address}{route.end_address && route.end_address !== route.start_address ? ` → ${route.end_address}` : ''}
    </div>
  {/if}

  <!-- Note preview -->
  {#if route.notes}
    <p class="px-3 pt-1 text-xs text-surface-500 truncate">{route.notes.split('\n')[0]}</p>
  {/if}

  <!-- Metadata row -->
  <div class="px-3 py-2.5 flex items-center gap-3 flex-wrap text-sm">
    <div class="flex items-center gap-2 min-w-0">
      <span class="text-surface-100 font-medium truncate">
        {formatDate(route.create_time)} {formatTimeRange(route.start_time, route.end_time)}
      </span>
    </div>

    <div class="flex items-center gap-2 text-xs text-surface-400 ml-auto">
      {#if durationMin != null}
        <span>{formatDuration(durationMin)}</span>
      {/if}
      {#if route.distance != null}
        <span class="text-surface-600">&middot;</span>
        <span>{formatDistance(route.distance)}</span>
      {/if}
      {#if route.engagement_pct != null}
        <span class="text-surface-600">&middot;</span>
        <span class="text-engage-green">{route.engagement_pct}%</span>
      {/if}
      {#if route.is_preserved}
        <svg class="w-4 h-4 text-engage-blue" fill="currentColor" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" title="Preserved">
          <path d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/>
        </svg>
      {/if}
    </div>
  </div>
</button>
