<script>
  import { onMount, onDestroy } from 'svelte'

  /**
   * Leaflet GPS track map with:
   * - Polyline colored by engagement state (green/blue/grey)
   * - CircleMarker synced to video currentTime
   * - Auto-fit bounds on load
   */

  /** @type {{ coords: Array, events?: Array, currentTime?: number, durationMs?: number }} */
  let { coords = [], events = [], currentTime = 0, durationMs = 0 } = $props()

  let mapContainer = $state(null)
  let map = null
  let positionMarker = null
  let pathLayers = []

  // Dynamically import Leaflet (it accesses `window` on import)
  let L = null

  onMount(async () => {
    // Dynamic import avoids SSR issues and reduces initial bundle
    const leaflet = await import('leaflet')
    L = leaflet.default || leaflet

    // Import Leaflet CSS
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'
    document.head.appendChild(link)

    if (!mapContainer) return

    map = L.map(mapContainer, {
      zoomControl: true,
      attributionControl: false,
    })

    // Dark tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      subdomains: 'abcd',
    }).addTo(map)

    // Position marker
    positionMarker = L.circleMarker([0, 0], {
      radius: 6,
      fillColor: '#3b82f6',
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
      for (const [s, e] of engagedRanges) if (timeMs >= s && timeMs <= e) return '#22c55e'
      for (const [s, e] of overrideRanges) if (timeMs >= s && timeMs <= e) return '#3b82f6'
      return '#6b7280'
    }

    // Build colored segments — group consecutive points by color
    let currentColor = null
    let segment = []

    for (const pt of coords) {
      const color = getColor(pt.t * 1000)
      if (color !== currentColor && segment.length > 1) {
        const line = L.polyline(segment, { color: currentColor, weight: 3, opacity: 0.8 })
        line.addTo(map)
        pathLayers.push(line)
        segment = [segment[segment.length - 1]] // Keep last point for continuity
      }
      currentColor = color
      segment.push([pt.lat, pt.lng])
    }
    // Final segment
    if (segment.length > 1) {
      const line = L.polyline(segment, { color: currentColor, weight: 3, opacity: 0.8 })
      line.addTo(map)
      pathLayers.push(line)
    }

    // Fit bounds
    const allPoints = coords.map(c => [c.lat, c.lng])
    if (allPoints.length > 0) {
      map.fitBounds(L.latLngBounds(allPoints), { padding: [20, 20] })
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
      positionMarker.setLatLng([pt.lat, pt.lng])
    }
  })

  // Redraw path when coords/events change
  $effect(() => {
    if (coords.length > 0 && L) drawPath()
  })

  onDestroy(() => {
    if (map) {
      map.remove()
      map = null
    }
  })
</script>

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
