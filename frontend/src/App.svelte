<script>
  import { onMount } from 'svelte'
  import { dongleId, selectedRoute } from './lib/stores.js'
  import { fetchDevices } from './lib/api.js'
  import DeviceHeader from './lib/components/DeviceHeader.svelte'
  import RouteListPage from './lib/pages/RouteListPage.svelte'
  import RouteDetailPage from './lib/pages/RouteDetailPage.svelte'
  import TileManager from './lib/pages/TileManager.svelte'
  import SettingsPage from './lib/pages/SettingsPage.svelte'
  import DashboardPage from './lib/pages/DashboardPage.svelte'
  import SignalBrowserPage from './lib/pages/SignalBrowserPage.svelte'

  let error = $state(null)
  let page = $state('routes')  // 'routes' | 'tiles' | 'settings' | 'dashboard' | 'signals'

  function parseRoutePath() {
    // URL: /{dongleId}/{localId}/{start?}/{end?}
    const parts = location.pathname.split('/').filter(Boolean)
    if (parts[0] === 'tiles') return null
    return parts.length >= 2 ? parts[1] : null  // local_id
  }

  function parsePage() {
    const parts = location.pathname.split('/').filter(Boolean)
    if (parts[0] === 'tiles') return 'tiles'
    if (parts[0] === 'settings') return 'settings'
    if (parts[0] === 'dashboard') return 'dashboard'
    if (parts[0] === 'signals') return 'signals'
    return 'routes'
  }

  onMount(async () => {
    try {
      const devices = await fetchDevices()
      if (devices?.length > 0) {
        dongleId.set(devices[0].dongle_id)
      }
    } catch (e) {
      error = e.message
    }

    // Restore state from URL on load
    page = parsePage()
    const initialRoute = parseRoutePath()
    if (initialRoute) selectedRoute.set(initialRoute)

    // Sync selectedRoute → URL (pushState only on route switch)
    let lastRoute = initialRoute
    const unsub = selectedRoute.subscribe(route => {
      if (route === lastRoute) return
      lastRoute = route
      if (!route && page === 'routes') {
        history.pushState(null, '', '/')
      }
    })

    window.addEventListener('popstate', () => {
      page = parsePage()
      const route = parseRoutePath()
      lastRoute = route
      selectedRoute.set(route)
    })

    return unsub
  })

  function showRoutes() {
    page = 'routes'
    selectedRoute.set(null)
    history.pushState(null, '', '/')
  }

  function showTiles() {
    page = 'tiles'
    selectedRoute.set(null)
    history.pushState(null, '', '/tiles')
  }

  function showSettings() {
    page = 'settings'
    selectedRoute.set(null)
    history.pushState(null, '', '/settings')
  }

  function showDashboard() {
    page = 'dashboard'
    selectedRoute.set(null)
    history.pushState(null, '', '/dashboard')
  }
</script>

<div class="min-h-dvh flex flex-col">
  <DeviceHeader>
    {#snippet nav()}
      <div class="flex items-center gap-1">
        <button
          class="px-3 py-1.5 text-sm rounded transition-colors {page === 'routes' && !$selectedRoute ? 'bg-surface-700 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
          onclick={showRoutes}
        >
          Routes
        </button>
        <button
          class="px-3 py-1.5 text-sm rounded transition-colors {page === 'tiles' ? 'bg-surface-700 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
          onclick={showTiles}
        >
          Map Tiles
        </button>
        <button
          class="px-3 py-1.5 text-sm rounded transition-colors {page === 'dashboard' ? 'bg-surface-700 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
          onclick={showDashboard}
        >
          Dashboard
        </button>
        <button
          class="px-3 py-1.5 text-sm rounded transition-colors {page === 'settings' ? 'bg-surface-700 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
          onclick={showSettings}
        >
          Settings
        </button>
      </div>
    {/snippet}
  </DeviceHeader>

  <main class="flex-1 flex flex-col">
    {#if page === 'signals'}
      <SignalBrowserPage />
    {:else if error}
      <div class="flex items-center justify-center h-64">
        <div class="text-center">
          <p class="text-engage-red text-lg mb-2">Connection Error</p>
          <p class="text-surface-400 text-sm">{error}</p>
          <button class="btn-ghost mt-4" onclick={() => location.reload()}>
            Retry
          </button>
        </div>
      </div>
    {:else if page === 'dashboard'}
      <DashboardPage />
    {:else if page === 'settings'}
      <SettingsPage />
    {:else if page === 'tiles'}
      <TileManager />
    {:else if $selectedRoute}
      <RouteDetailPage />
    {:else}
      <RouteListPage />
    {/if}
  </main>
</div>
