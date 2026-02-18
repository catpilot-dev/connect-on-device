<script>
  import { onMount } from 'svelte'
  import { dongleId, selectedRoute } from './lib/stores.js'
  import { fetchDevices } from './lib/api.js'
  import DeviceHeader from './lib/components/DeviceHeader.svelte'
  import RouteListPage from './lib/pages/RouteListPage.svelte'
  import RouteDetailPage from './lib/pages/RouteDetailPage.svelte'

  let error = $state(null)

  onMount(async () => {
    try {
      const devices = await fetchDevices()
      if (devices?.length > 0) {
        dongleId.set(devices[0].dongle_id)
      }
    } catch (e) {
      error = e.message
    }
  })
</script>

<div class="min-h-dvh flex flex-col">
  <DeviceHeader />

  <main class="flex-1">
    {#if error}
      <div class="flex items-center justify-center h-64">
        <div class="text-center">
          <p class="text-engage-red text-lg mb-2">Connection Error</p>
          <p class="text-surface-400 text-sm">{error}</p>
          <button class="btn-ghost mt-4" onclick={() => location.reload()}>
            Retry
          </button>
        </div>
      </div>
    {:else if $selectedRoute}
      <RouteDetailPage />
    {:else}
      <RouteListPage />
    {/if}
  </main>
</div>
