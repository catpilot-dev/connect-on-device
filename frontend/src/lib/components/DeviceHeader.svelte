<script>
  import { onMount } from 'svelte'
  import { dongleId } from '../stores.js'
  import { fetchStorage } from '../api.js'
  import { formatBytes, storageLevel } from '../format.js'

  let storage = $state(null)

  onMount(async () => {
    dongleId.subscribe(async (id) => {
      if (!id) return
      try {
        storage = await fetchStorage()
      } catch (e) {
        console.warn('DeviceHeader fetch error:', e)
      }
    })
  })

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
