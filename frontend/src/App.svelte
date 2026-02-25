<script>
  import { onMount } from 'svelte'
  import { dongleId, selectedRoute, isMetric } from './lib/stores.js'
  import { fetchDevices, fetchIsOnroad, fetchParams } from './lib/api.js'
  import DeviceHeader from './lib/components/DeviceHeader.svelte'
  import RouteListPage from './lib/pages/RouteListPage.svelte'
  import RouteDetailPage from './lib/pages/RouteDetailPage.svelte'
  import TileManager from './lib/pages/TileManager.svelte'
  import SettingsPage from './lib/pages/SettingsPage.svelte'
  import DashboardPage from './lib/pages/DashboardPage.svelte'
  import SignalBrowserPage from './lib/pages/SignalBrowserPage.svelte'

  let error = $state(null)
  let isOnroad = $state(false)
  function parsePage() {
    const parts = location.pathname.split('/').filter(Boolean)
    if (parts[0] === 'tiles') return 'tiles'
    if (parts[0] === 'settings') return 'settings'
    if (parts[0] === 'dashboard') return 'dashboard'
    if (parts[0] === 'signals') return 'signals'
    return 'routes'
  }

  let page = $state(parsePage())

  function parseRoutePath() {
    // URL: /{dongleId}/{localId}/{start?}/{end?}
    const parts = location.pathname.split('/').filter(Boolean)
    if (parts[0] === 'tiles') return null
    return parts.length >= 2 ? parts[1] : null  // local_id
  }

  onMount(async () => {
    // Fetch all startup data in parallel
    const [onroadResult, devicesResult, paramsResult] = await Promise.allSettled([
      fetchIsOnroad(),
      fetchDevices(),
      fetchParams(),
    ])
    isOnroad = onroadResult.status === 'fulfilled' ? onroadResult.value : false
    if (devicesResult.status === 'fulfilled' && devicesResult.value?.length > 0) {
      dongleId.set(devicesResult.value[0].dongle_id)
    } else if (devicesResult.status === 'rejected') {
      error = devicesResult.reason?.message ?? 'Connection error'
    }
    if (paramsResult.status === 'fulfilled') {
      isMetric.set(paramsResult.value.IsMetric !== '0')
    }

    // Restore state from URL on load
    page = isOnroad ? 'dashboard' : parsePage()
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
      const p = parsePage()
      if (isOnroad && (p === 'routes')) {
        page = 'dashboard'
        history.replaceState(null, '', '/dashboard')
        return
      }
      page = p
      const route = parseRoutePath()
      lastRoute = route
      selectedRoute.set(route)
    })

    return unsub
  })

  function showRoutes() {
    if (isOnroad) return
    page = 'routes'
    selectedRoute.set(null)
    history.pushState(null, '', '/')
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

{#if page === 'signals'}
  <SignalBrowserPage />
{:else if page === 'tiles'}
  <TileManager />
{:else}
  <div class="min-h-dvh flex flex-col">
    <DeviceHeader>
      {#snippet nav()}
        <div class="flex items-center gap-1">
          {#if !isOnroad}
          <button
            class="px-3 py-1.5 text-sm rounded transition-colors {page === 'routes' && !$selectedRoute ? 'bg-surface-700 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
            onclick={showRoutes}
          >
            Routes
          </button>
          {/if}
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
      {:else if page === 'dashboard'}
        <DashboardPage {isOnroad} />
      {:else if page === 'settings'}
        <SettingsPage {isOnroad} />
      {:else if isOnroad}
        <div class="flex items-center justify-center h-64">
          <div class="text-center">
            <p class="text-surface-400 text-lg">Routes unavailable while driving</p>
          </div>
        </div>
      {:else if $selectedRoute}
        <RouteDetailPage />
      {:else}
        <RouteListPage />
      {/if}
    </main>
  </div>
{/if}
