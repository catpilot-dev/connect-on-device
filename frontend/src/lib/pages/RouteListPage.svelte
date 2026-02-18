<script>
  import { onMount } from 'svelte'
  import { dongleId } from '../stores.js'
  import { fetchRoutes, fetchStorage } from '../api.js'
  import { formatBytes, storageLevel } from '../format.js'
  import RouteCard from '../components/RouteCard.svelte'

  let routes = $state([])
  let loading = $state(true)
  let loadingMore = $state(false)
  let hasMore = $state(true)
  let storage = $state(null)
  let error = $state(null)

  const PAGE_SIZE = 15

  async function loadRoutes(id) {
    loading = true
    error = null
    try {
      const [data, st] = await Promise.all([
        fetchRoutes(id, { limit: PAGE_SIZE }),
        fetchStorage(),
      ])
      routes = data
      storage = st
      hasMore = data.length >= PAGE_SIZE
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }

  async function loadMore() {
    if (loadingMore || !hasMore || routes.length === 0) return
    loadingMore = true
    try {
      const lastRoute = routes[routes.length - 1]
      const data = await fetchRoutes($dongleId, {
        limit: PAGE_SIZE,
        beforeCounter: lastRoute.route_counter,
      })
      routes = [...routes, ...data]
      hasMore = data.length >= PAGE_SIZE
    } catch (e) {
      console.error('loadMore error:', e)
    } finally {
      loadingMore = false
    }
  }

  onMount(() => {
    const unsub = dongleId.subscribe((id) => {
      if (id) loadRoutes(id)
    })
    return unsub
  })

  const level = $derived(storage ? storageLevel(storage.percent_free) : 'ok')
</script>

<div class="mx-auto max-w-3xl px-4 py-4 space-y-3">
  <!-- Storage warning banner -->
  {#if storage && storage.percent_free < 25}
    <div
      class="rounded-lg px-4 py-3 text-sm flex items-center gap-2 {level === 'warning' ? 'bg-amber-500/10 text-amber-400' : 'bg-red-500/10 text-red-400'}"
    >
      <svg class="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.168 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/>
      </svg>
      <span>
        Storage {level === 'critical' ? 'critically' : ''} low:
        {formatBytes(storage.free)} free of {formatBytes(storage.total)}
      </span>
    </div>
  {/if}

  <!-- Loading state -->
  {#if loading}
    <div class="space-y-3">
      {#each Array(5) as _}
        <div class="card animate-pulse">
          <div class="aspect-[21/3] bg-surface-700 rounded-t-lg"></div>
          <div class="px-3 py-2.5 flex gap-3">
            <div class="h-4 w-32 bg-surface-700 rounded"></div>
            <div class="h-4 w-20 bg-surface-700 rounded ml-auto"></div>
          </div>
        </div>
      {/each}
    </div>
  {:else if error}
    <div class="flex items-center justify-center h-48">
      <p class="text-surface-400">{error}</p>
    </div>
  {:else if routes.length === 0}
    <div class="flex items-center justify-center h-48">
      <p class="text-surface-400">No routes found</p>
    </div>
  {:else}
    {#each routes as route (route.fullname)}
      <RouteCard {route} />
    {/each}

    <!-- Load more -->
    {#if hasMore}
      <div class="flex justify-center py-4">
        <button
          class="btn-ghost"
          onclick={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading...' : 'Load more'}
        </button>
      </div>
    {/if}
  {/if}
</div>
