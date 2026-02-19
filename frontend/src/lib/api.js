// API layer for connect_on_device
// All fetch functions target the comma-compatible /v1 endpoints

/** Encode route fullname for URL path: "dongle/date" → "dongle|date" */
const encodeRouteName = (name) => name.replace('/', '|')

// ── Device & auth ───────────────────────────────────────────

export async function fetchDevices() {
  const res = await fetch('/v1/me/devices/')
  if (!res.ok) throw new Error(`fetchDevices: ${res.status}`)
  return res.json()
}

export async function fetchDeviceStats(dongleId) {
  const res = await fetch(`/v1.1/devices/${dongleId}/stats`)
  if (!res.ok) throw new Error(`fetchDeviceStats: ${res.status}`)
  return res.json()
}

export async function fetchStorage() {
  const res = await fetch('/v1/storage')
  if (!res.ok) throw new Error(`fetchStorage: ${res.status}`)
  return res.json()
}

// ── Routes ──────────────────────────────────────────────────

export async function fetchRoutes(dongleId, { limit = 25, beforeCounter } = {}) {
  let url = `/v1/devices/${dongleId}/routes?limit=${limit}`
  if (beforeCounter != null) url += `&before_counter=${beforeCounter}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`fetchRoutes: ${res.status}`)
  return res.json()
}

export async function fetchPreservedRoutes(dongleId) {
  const res = await fetch(`/v1/devices/${dongleId}/routes/preserved`)
  if (!res.ok) throw new Error(`fetchPreservedRoutes: ${res.status}`)
  return res.json()
}

export async function fetchRoute(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/`)
  if (!res.ok) throw new Error(`fetchRoute: ${res.status}`)
  return res.json()
}

export async function fetchRouteFiles(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/files`)
  if (!res.ok) throw new Error(`fetchRouteFiles: ${res.status}`)
  return res.json()
}

export async function enrichRoute(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/enrich`, { method: 'POST' })
  if (!res.ok) throw new Error(`enrichRoute: ${res.status}`)
  return res.json()
}

// ── Route actions ───────────────────────────────────────────

export async function preserveRoute(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/preserve`, { method: 'POST' })
  if (!res.ok) throw new Error(`preserveRoute: ${res.status}`)
  return res.json()
}

export async function unpreserveRoute(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/preserve`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`unpreserveRoute: ${res.status}`)
  return res.json()
}

export async function deleteRoute(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`deleteRoute: ${res.status}`)
  return res.json()
}

/** Build download URL with file type and segment selection */
export function downloadUrl(routeName, fileTypes = ['rlog'], segments = null) {
  let url = `/v1/route/${encodeRouteName(routeName)}/download?files=${fileTypes.join(',')}`
  if (segments && segments.length > 0) url += `&segments=${segments.join(',')}`
  return url
}

// ── Connectdata URL builders ────────────────────────────────

/** Build /connectdata/... URL for media files */
export function connectdataUrl(route, segment, filename) {
  if (route.url) {
    try {
      const u = new URL(route.url)
      return `${u.pathname}/${segment}/${filename}`
    } catch {
      return `${route.url}/${segment}/${filename}`
    }
  }
  const [dongle, date] = route.fullname.split('/')
  return `/connectdata/${dongle}/${date}/${segment}/${filename}`
}

/** Build HUD frame URL */
export function hudUrl(route, segment, offsetMs) {
  return connectdataUrl(route, segment, `hud?t=${Math.round(offsetMs)}`)
}

/** Sprite thumbnail URL for a segment */
export function spriteUrl(route, segment) {
  return connectdataUrl(route, segment, 'sprite.jpg')
}

// ── Derived data (coords, events) ───────────────────────────

export async function fetchCoords(route, segment) {
  const url = connectdataUrl(route, segment, 'coords.json')
  const res = await fetch(url)
  if (!res.ok) return []
  return res.json()
}

export async function fetchEvents(route, segment) {
  const url = connectdataUrl(route, segment, 'events.json')
  const res = await fetch(url)
  if (!res.ok) return []
  return res.json()
}

/** Fetch coords for all segments in parallel */
export async function fetchAllCoords(route) {
  const count = (route.maxqlog ?? 0) + 1
  const results = await Promise.all(
    Array.from({ length: count }, (_, i) =>
      fetchCoords(route, i).catch(() => [])
    )
  )
  return results.flat()
}

/** Fetch events for all segments in parallel */
export async function fetchAllEvents(route) {
  const count = (route.maxqlog ?? 0) + 1
  const results = await Promise.all(
    Array.from({ length: count }, (_, i) =>
      fetchEvents(route, i).catch(() => [])
    )
  )
  return results.flat()
}

/** Fetch events for all segments with progress callback */
export async function fetchAllEventsWithProgress(route, onProgress) {
  const count = (route.maxqlog ?? 0) + 1
  let done = 0
  const results = new Array(count)
  await Promise.all(
    Array.from({ length: count }, (_, i) =>
      fetchEvents(route, i).catch(() => []).then(data => {
        results[i] = data
        done++
        onProgress?.(done, count)
      })
    )
  )
  return results.flat()
}
