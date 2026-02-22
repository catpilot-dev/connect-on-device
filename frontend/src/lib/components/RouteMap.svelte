<script>
  import { onMount, onDestroy } from 'svelte'
  import { wgs84ToGcj02 } from '../gcj02.js'
  import { getTileSource, TILE_SOURCES } from '../tileSource.js'

  /**
   * Leaflet GPS track map with:
   * - Polyline colored by engagement state (green/blue/grey)
   * - CircleMarker synced to video currentTime
   * - Auto-fit bounds on load
   * - Configurable tile source (AMap for China, CartoDB for overseas)
   */

  /** @type {{ coords: Array, events?: Array, currentTime?: number, durationMs?: number, selectionStart?: number, selectionEnd?: number, visible?: boolean }} */
  let { coords = [], events = [], currentTime = 0, durationMs = 0, selectionStart = 0, selectionEnd = 0, visible = true } = $props()

  let mapContainer = $state(null)
  let map = null
  let positionMarker = null
  let pathLayers = []

  // Dynamically import Leaflet (it accesses `window` on import)
  let L = null
  let tileConfig = TILE_SOURCES[getTileSource()] || TILE_SOURCES.amap

  /** Convert coord to map projection — GCJ-02 for AMap, passthrough for WGS-84 tiles */
  function toMapCoord(lat, lng) {
    return tileConfig.gcj02 ? wgs84ToGcj02(lat, lng) : [lat, lng]
  }

  onMount(async () => {
    // Dynamic import avoids SSR issues and reduces initial bundle
    const leaflet = await import('leaflet')
    L = leaflet.default || leaflet

    // Import Leaflet CSS from npm package (bundled by Vite, no CDN dependency)
    await import('leaflet/dist/leaflet.css')

    if (!mapContainer) return

    map = L.map(mapContainer, {
      zoomControl: true,
      attributionControl: false,
    })

    // Tile layer from user preference (AMap for China, CartoDB for overseas)
    L.tileLayer(tileConfig.url, {
      maxZoom: tileConfig.maxZoom,
      subdomains: tileConfig.subdomains,
      className: tileConfig.className || undefined,
    }).addTo(map)

    // Position marker
    positionMarker = L.circleMarker([0, 0], {
      radius: 6,
      fillColor: '#80D8A6',
      fillOpacity: 1,
      color: '#ffffff',
      weight: 2,
    }).addTo(map)

    drawPath()
  })

  function drawPath() {
    if (!L || !map || coords.length === 0) return

    // Clear old layers
    for (const layer of pathLayers) map.removeLayer(layer)
    pathLayers = []

    // Build engagement lookup: time_ms → engaged boolean
    const engagedRanges = events
      .filter(e => e.type === 'engaged' && e.end_route_offset_millis != null)
      .map(e => [e.route_offset_millis, e.end_route_offset_millis])

    const overrideRanges = events
      .filter(e => e.type === 'overriding' && e.end_route_offset_millis != null)
      .map(e => [e.route_offset_millis, e.end_route_offset_millis])

    function getColor(timeMs) {
      for (const [s, e] of engagedRanges) if (timeMs >= s && timeMs <= e) return '#178644'
      for (const [s, e] of overrideRanges) if (timeMs >= s && timeMs <= e) return '#919B95'
      return '#173349'
    }

    const hasSelection = selectionStart > 0 || (selectionEnd > 0 && selectionEnd < (durationMs / 1000) - 0.5)

    // Build colored segments — group consecutive points by color + selection state
    let currentColor = null
    let currentInSel = null
    let segment = []

    for (const pt of coords) {
      const color = getColor(pt.t * 1000)
      const inSel = !hasSelection || (pt.t >= selectionStart && pt.t <= selectionEnd)

      if ((color !== currentColor || inSel !== currentInSel) && segment.length > 1) {
        const line = L.polyline(segment, {
          color: currentColor,
          weight: currentInSel ? 3 : 2,
          opacity: currentInSel ? 0.8 : 0.2,
        })
        line.addTo(map)
        pathLayers.push(line)
        segment = [segment[segment.length - 1]]
      }
      currentColor = color
      currentInSel = inSel
      segment.push(toMapCoord(pt.lat, pt.lng))
    }
    // Final segment
    if (segment.length > 1) {
      const line = L.polyline(segment, {
        color: currentColor,
        weight: currentInSel ? 3 : 2,
        opacity: currentInSel ? 0.8 : 0.2,
      })
      line.addTo(map)
      pathLayers.push(line)
    }

    // Fit bounds to selection if active, otherwise full route
    if (hasSelection) {
      const selPoints = coords
        .filter(c => c.t >= selectionStart && c.t <= selectionEnd)
        .map(c => toMapCoord(c.lat, c.lng))
      if (selPoints.length > 1) {
        map.fitBounds(L.latLngBounds(selPoints), { padding: [20, 20] })
      }
    } else {
      const allPoints = coords.map(c => toMapCoord(c.lat, c.lng))
      if (allPoints.length > 0) {
        map.fitBounds(L.latLngBounds(allPoints), { padding: [20, 20] })
      }
    }
  }

  // Update position marker when currentTime changes
  $effect(() => {
    if (!positionMarker || coords.length === 0 || !L) return

    // Binary search for nearest point
    const timeSec = currentTime
    let lo = 0, hi = coords.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (coords[mid].t < timeSec) lo = mid + 1
      else hi = mid
    }

    const pt = coords[lo]
    if (pt) {
      positionMarker.setLatLng(toMapCoord(pt.lat, pt.lng))
    }
  })

  // Redraw path when coords/events/selection change
  $effect(() => {
    // Track dependencies
    coords; events; selectionStart; selectionEnd;
    if (coords.length > 0 && L) drawPath()
  })

  // Fix Leaflet tile loading when tab becomes visible
  $effect(() => {
    if (visible && map) {
      // Delay to let the DOM layout settle after tab switch
      setTimeout(() => map?.invalidateSize(), 50)
    }
  })

  onDestroy(() => {
    if (map) {
      map.remove()
      map = null
    }
  })
</script>

<style>
  :global(.amap-dark) {
    filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(1.1);
  }
</style>

<div class="card overflow-hidden">
  {#if coords.length === 0}
    <div class="aspect-video bg-surface-800 flex items-center justify-center">
      <p class="text-surface-500 text-sm">No GPS data</p>
    </div>
  {:else}
    <div
      bind:this={mapContainer}
      class="aspect-video w-full"
      style="min-height: 200px"
    ></div>
  {/if}
</div>
