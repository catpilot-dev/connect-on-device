<script>
  import { selectedRoute } from '../stores.js'
  import { formatDate, formatTime, formatDuration, formatDistance } from '../format.js'
  import Filmstrip from './Filmstrip.svelte'

  /** @type {{ route: object }} */
  let { route } = $props()

  const durationMin = $derived(
    route.maxqlog != null ? route.maxqlog + 1 : null
  )

  function handleClick() {
    selectedRoute.set(route.fullname)
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
      {#if route.engagement_pct != null}
        <span class="text-surface-600">&middot;</span>
        <span class="text-engage-green">{route.engagement_pct}%</span>
      {/if}
      {#if route.is_preserved}
        <span title="Preserved" class="text-engage-blue">&#9733;</span>
      {/if}
    </div>
  </div>
</button>
