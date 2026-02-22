<script>
  import { onMount, onDestroy } from 'svelte'
  import { fetchTileList, startTileDownload, fetchTileProgress, cancelTileDownload, deleteTile } from '../api.js'
  import { wgs84ToGcj02 } from '../gcj02.js'
  import { tileLabel, tileName } from '../tile-names.js'
  import { getTileSource, TILE_SOURCES } from '../tileSource.js'

  let mapContainer = $state(null)
  let map = null
  let L = null
  let gridLayers = []
  const tileConfig = TILE_SOURCES[getTileSource()] || TILE_SOURCES.amap

  // State
  let downloaded = $state([])
  let selected = $state([])
  let storage = $state({ total_mb: 0, tile_count: 0 })
  let progress = $state({ active: false, total: 0, done: 0, current: null, error: null })
  let loading = $state(true)
  let error = $state(null)
  let pollTimer = null

  // Grid range for eastern China (covers most driving areas)
  const LAT_MIN = 18, LAT_MAX = 54
  const LON_MIN = 72, LON_MAX = 136
  const GRID_STEP = 2

  // Presets
  const PRESETS = [
    { name: 'Shanghai', tiles: [{ lat: 30, lon: 120 }] },
    { name: 'Yangtze Delta', tiles: [
      { lat: 30, lon: 118 }, { lat: 30, lon: 120 }, { lat: 30, lon: 122 },
      { lat: 32, lon: 118 }, { lat: 32, lon: 120 }, { lat: 32, lon: 122 },
      { lat: 28, lon: 118 }, { lat: 28, lon: 120 }, { lat: 28, lon: 122 },
    ]},
    { name: 'Beijing', tiles: [{ lat: 38, lon: 116 }, { lat: 40, lon: 116 }] },
    { name: 'Guangdong', tiles: [
      { lat: 22, lon: 112 }, { lat: 22, lon: 114 },
      { lat: 24, lon: 112 }, { lat: 24, lon: 114 },
    ]},
  ]

  function tileKey(lat, lon) { return `${lat},${lon}` }

  function isDownloaded(lat, lon) {
    return downloaded.some(t => t.lat === lat && t.lon === lon)
  }

  function isSelected(lat, lon) {
    return selected.some(t => t.lat === lat && t.lon === lon)
  }

  function toggleTile(lat, lon) {
    if (progress.active) return
    const key = tileKey(lat, lon)
    const idx = selected.findIndex(t => t.lat === lat && t.lon === lon)
    if (idx >= 0) {
      selected = selected.filter((_, i) => i !== idx)
    } else {
      selected = [...selected, { lat, lon }]
    }
    updateGrid()
  }

  function applyPreset(preset) {
    if (progress.active) return
    // Add preset tiles that aren't already downloaded or selected
    const newTiles = preset.tiles.filter(t => !isDownloaded(t.lat, t.lon) && !isSelected(t.lat, t.lon))
    selected = [...selected, ...newTiles]
    updateGrid()
  }

  function clearSelection() {
    selected = []
    updateGrid()
  }

  function tileStyle(lat, lon) {
    if (isDownloaded(lat, lon)) return { color: '#22c55e', weight: 3, fillOpacity: 0 }
    if (isSelected(lat, lon)) return { color: '#3b82f6', weight: 3, fillOpacity: 0 }
    return { color: '#3a4254', weight: 1, fillOpacity: 0 }
  }

  function updateGrid() {
    if (!L || !map) return

    // Remove old grid layers
    for (const layer of gridLayers) map.removeLayer(layer)
    gridLayers = []

    for (let lat = LAT_MIN; lat < LAT_MAX; lat += GRID_STEP) {
      for (let lon = LON_MIN; lon < LON_MAX; lon += GRID_STEP) {
        // Transform grid corners for tile alignment
        const sw = tileConfig.gcj02 ? wgs84ToGcj02(lat, lon) : [lat, lon]
        const ne = tileConfig.gcj02 ? wgs84ToGcj02(lat + GRID_STEP, lon + GRID_STEP) : [lat + GRID_STEP, lon + GRID_STEP]
        const bounds = [sw, ne]
        const style = tileStyle(lat, lon)

        const rect = L.rectangle(bounds, {
          color: style.color,
          weight: style.weight,
          fillColor: style.color,
          fillOpacity: style.fillOpacity,
          className: 'tile-rect',
        })

        rect.on('click', () => {
          if (!isDownloaded(lat, lon)) {
            toggleTile(lat, lon)
          }
        })

        // Tooltip
        const dl = isDownloaded(lat, lon)
        const size = dl ? downloaded.find(t => t.lat === lat && t.lon === lon)?.size_mb : null
        const name = tileName(lat, lon)
        let label = name ? `${name}` : `${lat}, ${lon}`
        if (dl) label += ` — ${size ?? '?'} MB`
        else if (isSelected(lat, lon)) label += ' — selected'
        rect.bindTooltip(label, { sticky: true, className: 'tile-tooltip' })

        rect.addTo(map)
        gridLayers.push(rect)
      }
    }
  }

  async function refresh() {
    try {
      const data = await fetchTileList()
      downloaded = data.tiles
      storage = data.storage
      updateGrid()
    } catch (e) {
      error = e.message
    }
  }

  async function startDownload() {
    if (selected.length === 0 || progress.active) return
    error = null
    try {
      await startTileDownload(selected)
      startPolling()
    } catch (e) {
      error = e.message
    }
  }

  async function cancelDownload() {
    try {
      await cancelTileDownload()
    } catch (e) {
      error = e.message
    }
  }

  async function handleDelete(lat, lon) {
    try {
      await deleteTile(lat, lon)
      await refresh()
    } catch (e) {
      error = e.message
    }
  }

  function startPolling() {
    stopPolling()
    pollTimer = setInterval(async () => {
      try {
        progress = await fetchTileProgress()
        if (!progress.active) {
          stopPolling()
          // Remove successfully downloaded tiles from selection
          selected = selected.filter(t => !isDownloaded(t.lat, t.lon))
          await refresh()
        }
      } catch (e) {
        console.warn('poll error:', e)
      }
    }, 1000)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  onMount(async () => {
    // Dynamic Leaflet import
    const leaflet = await import('leaflet')
    L = leaflet.default || leaflet

    await import('leaflet/dist/leaflet.css')

    if (!mapContainer) return

    // Center on China for AMap, wider view for CartoDB
    const center = tileConfig.gcj02 ? wgs84ToGcj02(32, 118) : [32, 118]
    map = L.map(mapContainer, {
      zoomControl: true,
      attributionControl: false,
      center,
      zoom: 5,
    })

    L.tileLayer(tileConfig.url, {
      maxZoom: tileConfig.maxZoom,
      subdomains: tileConfig.subdomains,
      className: tileConfig.className || undefined,
    }).addTo(map)

    try {
      const data = await fetchTileList()
      downloaded = data.tiles
      storage = data.storage

      // Check if download is active
      progress = await fetchTileProgress()
      if (progress.active) startPolling()
    } catch (e) {
      error = e.message
    }

    loading = false
    updateGrid()
  })

  onDestroy(() => {
    stopPolling()
    if (map) {
      map.remove()
      map = null
    }
  })

  const progressPct = $derived(
    progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0
  )

  const currentTileLabel = $derived(
    progress.current
      ? tileLabel(...progress.current.split(',').map(Number))
      : '...'
  )
</script>

<style>
  :global(.amap-dark) {
    filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(1.1);
  }
  :global(.tile-rect) {
    cursor: pointer;
    transition: fill-opacity 0.15s;
  }
  :global(.tile-rect:hover) {
    fill-opacity: 0.5 !important;
  }
  :global(.tile-tooltip) {
    background: #161b28;
    color: #d1d5de;
    border: 1px solid #2a3040;
    font-size: 12px;
    padding: 3px 8px;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }
  :global(.tile-tooltip::before) {
    border-top-color: #2a3040 !important;
  }
</style>

<div class="flex flex-col h-full">
  <!-- Map -->
  <div
    bind:this={mapContainer}
    class="w-full flex-1"
    style="min-height: 400px"
  ></div>

  <!-- Controls panel -->
  <div class="bg-surface-900 border-t border-surface-700/50 p-4 space-y-3">
    <!-- Error -->
    {#if error}
      <div class="text-engage-red text-sm">{error}</div>
    {/if}

    <!-- Progress bar -->
    {#if progress.active}
      <div class="space-y-1">
        <div class="flex items-center justify-between text-xs text-surface-300">
          <span>Downloading {currentTileLabel}</span>
          <span>{progress.done}/{progress.total} tiles ({progressPct}%)</span>
        </div>
        <div class="w-full h-2 rounded-full bg-surface-700 overflow-hidden">
          <div
            class="h-full rounded-full bg-engage-blue transition-all duration-300"
            style="width: {progressPct}%"
          ></div>
        </div>
      </div>
    {/if}

    <!-- Presets row -->
    <div class="flex items-center gap-2 flex-wrap">
      <span class="text-xs text-surface-400">Presets:</span>
      {#each PRESETS as preset}
        <button
          class="px-2.5 py-1 text-xs rounded bg-surface-700 text-surface-200 hover:bg-surface-600 transition-colors disabled:opacity-40"
          disabled={progress.active}
          onclick={() => applyPreset(preset)}
        >
          {preset.name}
        </button>
      {/each}
      {#if selected.length > 0}
        <button
          class="px-2.5 py-1 text-xs rounded bg-surface-700 text-surface-400 hover:text-surface-200 hover:bg-surface-600 transition-colors"
          onclick={clearSelection}
        >
          Clear
        </button>
      {/if}
    </div>

    <!-- Action bar -->
    <div class="flex items-center justify-between">
      <div class="text-sm text-surface-300">
        <span class="text-surface-50 font-medium">{storage.tile_count}</span> tiles
        <span class="text-surface-500 mx-1">|</span>
        <span class="text-surface-50 font-medium">{storage.total_mb}</span> MB
        {#if selected.length > 0}
          <span class="text-surface-500 mx-1">|</span>
          <span class="text-engage-blue font-medium">{selected.length}</span> selected
        {/if}
      </div>

      <div class="flex items-center gap-2">
        {#if progress.active}
          <button
            class="px-4 py-2 text-sm rounded bg-engage-red/20 text-engage-red hover:bg-engage-red/30 transition-colors"
            onclick={cancelDownload}
          >
            Cancel
          </button>
        {:else}
          <button
            class="px-4 py-2 text-sm rounded bg-engage-blue text-white hover:bg-engage-blue/80 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={selected.length === 0}
            onclick={startDownload}
          >
            Download {selected.length} tile{selected.length !== 1 ? 's' : ''}
          </button>
        {/if}
      </div>
    </div>

    <!-- Downloaded tiles list -->
    {#if downloaded.length > 0}
      <details class="text-sm">
        <summary class="text-surface-400 cursor-pointer hover:text-surface-200 transition-colors">
          Downloaded tiles
        </summary>
        <div class="mt-2 space-y-1 max-h-40 overflow-y-auto">
          {#each downloaded as tile}
            <div class="flex items-center justify-between px-2 py-1 rounded bg-surface-800">
              <span class="text-surface-200 text-xs">{tileLabel(tile.lat, tile.lon)}</span>
              <div class="flex items-center gap-2">
                <span class="text-surface-400 text-xs">{tile.size_mb} MB</span>
                <button
                  class="text-xs text-engage-red/60 hover:text-engage-red transition-colors"
                  onclick={() => handleDelete(tile.lat, tile.lon)}
                >
                  Delete
                </button>
              </div>
            </div>
          {/each}
        </div>
      </details>
    {/if}

    {#if progress.error}
      <div class="text-engage-red text-xs p-2 rounded bg-engage-red/10">{progress.error}</div>
    {/if}
  </div>
</div>
