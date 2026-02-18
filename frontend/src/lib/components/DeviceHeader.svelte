<script>
  import { onMount } from 'svelte'
  import { dongleId } from '../stores.js'
  import { fetchDeviceStats, fetchStorage } from '../api.js'
  import { formatDistance, formatDuration, formatBytes, storageLevel } from '../format.js'

  let stats = $state(null)
  let storage = $state(null)

  onMount(async () => {
    dongleId.subscribe(async (id) => {
      if (!id) return
      try {
        const [s, st] = await Promise.all([
          fetchDeviceStats(id),
          fetchStorage(),
        ])
        stats = s
        storage = st
      } catch (e) {
        console.warn('DeviceHeader fetch error:', e)
      }
    })
  })

  const engagedPct = $derived(
    stats?.all?.total_minutes_with_events > 0
      ? Math.round((stats.all.engaged_minutes / stats.all.total_minutes_with_events) * 100)
      : 0
  )

  const storagePct = $derived(storage ? Math.round(100 - storage.percent_free) : 0)
  const level = $derived(storage ? storageLevel(storage.percent_free) : 'ok')
</script>

<header class="sticky top-0 z-50 border-b border-surface-700/50 bg-surface-900/80 backdrop-blur-lg">
  <div class="mx-auto max-w-6xl px-4 py-3 flex items-center gap-4 flex-wrap">
    <!-- Brand -->
    <div class="flex items-center gap-2 mr-auto">
      <span class="text-lg font-semibold text-surface-50">connect</span>
      {#if $dongleId}
        <span class="badge bg-surface-700 text-surface-300">{$dongleId}</span>
      {/if}
    </div>

    <!-- Stats chips -->
    {#if stats}
      <div class="hidden sm:flex items-center gap-3 text-xs text-surface-400">
        <span>{stats.all.routes} routes</span>
        <span class="text-surface-600">|</span>
        <span>{formatDistance(stats.all.distance)}</span>
        <span class="text-surface-600">|</span>
        <span>{formatDuration(stats.all.minutes)}</span>
        <span class="text-surface-600">|</span>
        <span class="text-engage-green">{engagedPct}% engaged</span>
      </div>
    {/if}

    <!-- Storage bar -->
    {#if storage}
      <div class="flex items-center gap-2 text-xs">
        <div class="w-24 h-1.5 rounded-full bg-surface-700 overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500"
            class:bg-engage-green={level === 'ok'}
            class:bg-engage-orange={level === 'warning'}
            class:bg-engage-red={level === 'critical'}
            style="width: {storagePct}%"
          ></div>
        </div>
        <span class="text-surface-400 whitespace-nowrap">
          {formatBytes(storage.used)} / {formatBytes(storage.total)}
        </span>
      </div>
    {/if}
  </div>
</header>
