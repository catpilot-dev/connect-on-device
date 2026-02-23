<script>
  import { onMount, onDestroy } from 'svelte'
  import { fetchTileList, startTileDownload, fetchTileProgress, cancelTileDownload, deleteTile } from '../api.js'
  import { wgs84ToGcj02 } from '../gcj02.js'
  import { tileLabel, tileName } from '../tile-names.js'
  import { getTileSource, TILE_SOURCES } from '../tileSource.js'
  import ChevronIcon from '../components/ChevronIcon.svelte'

  let mapContainer = $state(null)
  let map = null
  let L = null
  let gridLayers = []
  const tileConfig = TILE_SOURCES[getTileSource()] || TILE_SOURCES.amap

  // State
  let downloaded = $state([])
  let selected = $state([])
  let selectedForDelete = $state([])
  let storage = $state({ total_mb: 0, tile_count: 0 })
  let progress = $state({ active: false, total: 0, done: 0, current: null, error: null })
  let loading = $state(true)
  let error = $state(null)
  let pollTimer = null
  let highlightedTile = $state(null) // {lat, lon} of tile hovered in list
  let dlExpanded = $state(false)
  let dlContainer = $state(null)
  let gridLayerMap = {}  // "lat,lon" -> Leaflet rectangle layer

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

  function isSelectedForDelete(lat, lon) {
    return selectedForDelete.some(t => t.lat === lat && t.lon === lon)
  }

  function toggleTile(lat, lon) {
    if (progress.active) return
    if (isDownloaded(lat, lon)) {
      // Toggle delete selection for downloaded tiles
      const idx = selectedForDelete.findIndex(t => t.lat === lat && t.lon === lon)
      if (idx >= 0) {
        selectedForDelete = selectedForDelete.filter((_, i) => i !== idx)
      } else {
        selectedForDelete = [...selectedForDelete, { lat, lon }]
      }
    } else {
      // Toggle download selection for empty tiles
      const idx = selected.findIndex(t => t.lat === lat && t.lon === lon)
      if (idx >= 0) {
        selected = selected.filter((_, i) => i !== idx)
      } else {
        selected = [...selected, { lat, lon }]
      }
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
    selectedForDelete = []
    updateGrid()
  }

  function tileStyle(lat, lon) {
    if (isSelectedForDelete(lat, lon)) return { color: '#ef4444', weight: 3, fillOpacity: 0.2 }
    if (isDownloaded(lat, lon)) return { color: '#22c55e', weight: 3, fillOpacity: 0.01 }
    if (isSelected(lat, lon)) return { color: '#3b82f6', weight: 3, fillOpacity: 0.01 }
    return { color: '#3a4254', weight: 1, fillOpacity: 0.01 }
  }

  function highlightTileOnMap(lat, lon) {
    highlightedTile = { lat, lon }
    const key = tileKey(lat, lon)
    const layer = gridLayerMap[key]
    if (layer) {
      layer.setStyle({ color: '#ef4444', weight: 3, fillColor: '#ef4444', fillOpacity: 0.25 })
      layer.bringToFront()
    }
  }

  function clearHighlight() {
    if (!highlightedTile) return
    const key = tileKey(highlightedTile.lat, highlightedTile.lon)
    const layer = gridLayerMap[key]
    if (layer) {
      const style = tileStyle(highlightedTile.lat, highlightedTile.lon)
      layer.setStyle({ color: style.color, weight: style.weight, fillColor: style.color, fillOpacity: style.fillOpacity })
    }
    highlightedTile = null
  }

  function updateGrid() {
    if (!L || !map) return

    // Remove old grid layers
    for (const layer of gridLayers) map.removeLayer(layer)
    gridLayers = []
    gridLayerMap = {}

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
          toggleTile(lat, lon)
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
        gridLayerMap[tileKey(lat, lon)] = rect
      }
    }

    // Bring selected tiles to front so borders aren't overlapped by neighbors
    for (const t of selected) {
      gridLayerMap[tileKey(t.lat, t.lon)]?.bringToFront()
    }
    for (const t of selectedForDelete) {
      gridLayerMap[tileKey(t.lat, t.lon)]?.bringToFront()
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

  async function handleDeleteSelected() {
    if (selectedForDelete.length === 0) return
    error = null
    try {
      for (const t of selectedForDelete) {
        await deleteTile(t.lat, t.lon)
      }
      selectedForDelete = []
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
          await refresh()
          // Remove successfully downloaded tiles from selection (after refresh updates downloaded list)
          selected = selected.filter(t => !isDownloaded(t.lat, t.lon))
          updateGrid()
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

  // Close Downloaded list on outside click
  $effect(() => {
    if (!dlExpanded) return
    function onClick(e) {
      if (dlContainer && !dlContainer.contains(e.target)) dlExpanded = false
    }
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  })
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

<div class="flex flex-col h-full overflow-y-auto">
  <!-- Map (responsive with padding) -->
  <div class="flex-1 px-4 pt-4">
    <div
      bind:this={mapContainer}
      class="w-full h-full rounded-lg overflow-hidden"
      style="min-height: 400px"
    ></div>
  </div>

  <!-- Two-column: Downloaded Tiles (left) | Download Button (right) -->
  <div class="px-4 py-3 grid grid-cols-2 gap-4">

    <!-- Left: Downloaded tiles (collapsed by default) -->
    <div bind:this={dlContainer}>
      {#if error}
        <div class="text-engage-red text-xs mb-2">{error}</div>
      {/if}
      {#if progress.error}
        <div class="text-engage-red text-xs p-1.5 rounded bg-engage-red/10 mb-2">{progress.error}</div>
      {/if}

      <button
        class="w-full flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-2 text-sm rounded-lg bg-surface-700 hover:bg-surface-650 transition-colors"
        onclick={() => { dlExpanded = !dlExpanded }}
      >
        <span class="text-surface-200 text-sm">Downloaded</span>
        <span class="text-xs text-surface-500 ml-auto sm:ml-0 hidden sm:inline">{storage.tile_count} tiles &middot; {storage.total_mb} MB</span>
        <ChevronIcon rotated={dlExpanded} class="ml-auto" />
      </button>
      {#if dlExpanded}
        <div class="mt-1 space-y-1 max-h-48 overflow-y-auto">
          {#if downloaded.length === 0}
            <div class="text-xs text-surface-500 p-2 text-center">No tiles downloaded</div>
          {:else}
            {#each downloaded as tile}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <div
                class="flex items-center justify-between px-2 py-1.5 rounded transition-colors {highlightedTile?.lat === tile.lat && highlightedTile?.lon === tile.lon ? 'bg-engage-red/15' : 'bg-surface-800 hover:bg-surface-750'}"
                onmouseenter={() => highlightTileOnMap(tile.lat, tile.lon)}
                onmouseleave={clearHighlight}
              >
                <div class="min-w-0">
                  <div class="text-surface-200 text-xs truncate">{tileLabel(tile.lat, tile.lon)}</div>
                  <div class="text-surface-500 text-xs">{tile.size_mb} MB</div>
                </div>
                <button
                  class="shrink-0 ml-2 text-xs text-engage-red/60 hover:text-engage-red transition-colors"
                  onclick={() => handleDelete(tile.lat, tile.lon)}
                  title="Delete tile"
                >
                  <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clip-rule="evenodd" />
                  </svg>
                </button>
              </div>
            {/each}
          {/if}
        </div>
      {/if}
    </div>

    <!-- Right: Download button -->
    <div class="space-y-1.5">
      {#if progress.active}
        <div class="space-y-1.5">
          <div class="flex items-center justify-between text-xs text-surface-300">
            <span class="truncate">{currentTileLabel}</span>
            <span class="shrink-0">{progressPct}%</span>
          </div>
          <div class="w-full h-1.5 rounded-full bg-surface-700 overflow-hidden">
            <div
              class="h-full rounded-full bg-engage-blue transition-all duration-300"
              style="width: {progressPct}%"
            ></div>
          </div>
          <button
            class="w-full py-2 text-sm rounded-lg bg-engage-red/20 text-engage-red hover:bg-engage-red/30 transition-colors"
            onclick={cancelDownload}
          >Cancel</button>
        </div>
      {:else if selectedForDelete.length > 0}
        <button
          class="w-full py-2 text-sm rounded-lg transition-colors bg-engage-red/20 text-engage-red hover:bg-engage-red/30"
          onclick={handleDeleteSelected}
        >
          {selectedForDelete.length} selected, Delete
        </button>
        <button
          class="w-full py-1 text-xs text-surface-500 hover:text-surface-300 transition-colors"
          onclick={() => { selectedForDelete = []; updateGrid() }}
        >Clear selection</button>
      {:else}
        <button
          class="w-full py-2 text-sm rounded-lg transition-colors {selected.length > 0 ? 'bg-engage-blue text-white hover:bg-engage-blue/80' : 'bg-surface-700 text-surface-200 border border-dashed border-surface-500'}"
          disabled={selected.length === 0}
          onclick={startDownload}
        >
          {#if selected.length > 0}
            {selected.length} selected, Download
          {:else}
            Click map to select
          {/if}
        </button>
        {#if selected.length > 0}
          <button
            class="w-full py-1 text-xs text-surface-500 hover:text-surface-300 transition-colors"
            onclick={clearSelection}
          >Clear selection</button>
        {/if}
      {/if}
    </div>

  </div>
</div>
