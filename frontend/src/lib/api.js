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

export async function saveNote(routeName, note) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/note`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  })
  if (!res.ok) throw new Error(`saveNote: ${res.status}`)
  return res.json()
}

export async function takeScreenshot(routeName, timeSec) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/screenshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ time: timeSec }),
  })
  if (!res.ok) throw new Error(`screenshot: ${res.status}`)
  return res  // Raw response for blob download
}

/** Build GET URL for a fcamera frame at the given time (opens as JPEG in browser) */
export function frameUrl(routeName, timeSec) {
  return `/v1/route/${encodeRouteName(routeName)}/frame?t=${timeSec.toFixed(2)}`
}

/** Build download URL with file type and segment selection */
export function downloadUrl(routeName, fileTypes = ['rlog'], segments = null) {
  let url = `/v1/route/${encodeRouteName(routeName)}/download?files=${fileTypes.join(',')}`
  if (segments && segments.length > 0) url += `&segments=${segments.join(',')}`
  return url
}

// ── HUD pre-render ─────────────────────────────────────────

/**
 * Start HUD video prerender.
 * @param {string} routeName
 * @param {number} start - start time in seconds
 * @param {number} end - end time in seconds
 * @param {object} params - render params {speed, scale, fps}
 */
export async function prerenderHud(routeName, start, end, params = {}) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/hud/prerender`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end, ...params }),
  })
  if (!res.ok) throw new Error(`prerenderHud: ${res.status}`)
  return res.json()
}

export async function cancelHudRender(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/hud/cancel`, { method: 'POST' })
  if (!res.ok) throw new Error(`cancelHudRender: ${res.status}`)
  return res.json()
}

export async function hudProgress(routeName) {
  const res = await fetch(`/v1/route/${encodeRouteName(routeName)}/hud/progress`)
  if (!res.ok) throw new Error(`hudProgress: ${res.status}`)
  return res.json()
}

export function hudVideoUrl(routeName) {
  return `/v1/route/${encodeRouteName(routeName)}/hud/video`
}

// ── HUD live streaming ────────────────────────────────────

export async function startHudStream(routeName, start = 0) {
  const res = await fetch('/v1/hud/stream/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ route: encodeRouteName(routeName), start }),
  })
  if (!res.ok) throw new Error(`startHudStream: ${res.status}`)
  return res.json()
}

export async function stopHudStream() {
  const res = await fetch('/v1/hud/stream/stop', { method: 'POST' })
  if (!res.ok) throw new Error(`stopHudStream: ${res.status}`)
  return res.json()
}

export async function hudStreamStatus() {
  const res = await fetch('/v1/hud/stream/status')
  if (!res.ok) throw new Error(`hudStreamStatus: ${res.status}`)
  return res.json()
}

/** HLS live stream playlist URL */
export function hudStreamUrl() {
  return '/v1/hud/stream/stream.m3u8'
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

// ── Software update management ──────────────────────────────

export async function fetchSoftware() {
  const res = await fetch('/v1/software')
  if (!res.ok) throw new Error(`fetchSoftware: ${res.status}`)
  return res.json()
}

export async function softwareCheck() {
  const res = await fetch('/v1/software/check', { method: 'POST' })
  if (!res.ok) throw new Error(`softwareCheck: ${res.status}`)
  return res.json()
}

export async function softwareDownload() {
  const res = await fetch('/v1/software/download', { method: 'POST' })
  if (!res.ok) throw new Error(`softwareDownload: ${res.status}`)
  return res.json()
}

export async function softwareInstall() {
  const res = await fetch('/v1/software/install', { method: 'POST' })
  if (!res.ok) throw new Error(`softwareInstall: ${res.status}`)
  return res.json()
}

export async function softwareBranch(branch) {
  const res = await fetch('/v1/software/branch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ branch }),
  })
  if (!res.ok) throw new Error(`softwareBranch: ${res.status}`)
  return res.json()
}

export async function softwareUninstall() {
  const res = await fetch('/v1/software/uninstall', { method: 'POST' })
  if (!res.ok) throw new Error(`softwareUninstall: ${res.status}`)
  return res.json()
}

// ── Lateral delay ───────────────────────────────────────────

export async function fetchLateralDelay() {
  const res = await fetch('/v1/lateral-delay')
  if (!res.ok) throw new Error(`fetchLateralDelay: ${res.status}`)
  return res.json()
}

// ── BMW params ─────────────────────────────────────────────

export async function fetchParams() {
  const res = await fetch('/v1/params')
  if (!res.ok) throw new Error(`fetchParams: ${res.status}`)
  return res.json()
}

export async function setParam(key, value) {
  const res = await fetch('/v1/params', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, value }),
  })
  if (!res.ok) throw new Error(`setParam: ${res.status}`)
  return res.json()
}

// ── Model management ──────────────────────────────────────

export async function fetchModels() {
  const res = await fetch('/v1/models')
  if (!res.ok) throw new Error(`fetchModels: ${res.status}`)
  return res.json()
}

export async function swapModel(type, modelId) {
  const res = await fetch('/v1/models/swap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, model_id: modelId }),
  })
  if (!res.ok) throw new Error(`swapModel: ${res.status}`)
  return res.json()
}

export async function checkModelUpdates() {
  const res = await fetch('/v1/models/check-updates', { method: 'POST' })
  if (!res.ok) throw new Error(`checkModelUpdates: ${res.status}`)
  return res.json()
}

export async function downloadModel(type, modelId) {
  const res = await fetch('/v1/models/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, model_id: modelId }),
  })
  if (!res.ok) throw new Error(`downloadModel: ${res.status}`)
  return res.json()
}

// ── OSM tile management ────────────────────────────────────

export async function fetchTileList() {
  const res = await fetch('/v1/mapd/tiles')
  if (!res.ok) throw new Error(`fetchTileList: ${res.status}`)
  return res.json()
}

export async function startTileDownload(tiles) {
  const res = await fetch('/v1/mapd/tiles/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tiles }),
  })
  if (!res.ok) throw new Error(`startTileDownload: ${res.status}`)
  return res.json()
}

export async function fetchTileProgress() {
  const res = await fetch('/v1/mapd/tiles/progress')
  if (!res.ok) throw new Error(`fetchTileProgress: ${res.status}`)
  return res.json()
}

export async function cancelTileDownload() {
  const res = await fetch('/v1/mapd/tiles/cancel', { method: 'POST' })
  if (!res.ok) throw new Error(`cancelTileDownload: ${res.status}`)
  return res.json()
}

export async function deleteTile(lat, lon) {
  const res = await fetch(`/v1/mapd/tiles/${lat}/${lon}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`deleteTile: ${res.status}`)
  return res.json()
}
