<script>
  import { onMount, onDestroy } from 'svelte'
  import { fetchRoute, fetchSignalAll } from '../api.js'
  import { formatVideoTime } from '../format.js'

  // ── Route parsing from URL ──
  function parseSignalsRoute() {
    const parts = location.pathname.split('/').filter(Boolean)
    if (parts[0] === 'signals' && parts.length >= 3) {
      return {
        dongleId: parts[1],
        localId: parts[2],
        selStart: parseInt(parts[3]) || 0,
        selEnd: parseInt(parts[4]) || 0,
      }
    }
    return null
  }

  const params = parseSignalsRoute()
  const routeId = params?.localId ?? ''
  let selectionStart = $state(params?.selStart ?? 0)
  let selectionEnd = $state(params?.selEnd ?? 0)

  let route = $state(null)
  let catalog = $state(null)
  let allData = $state(null)       // {msgType: [{t, ...}, ...]}
  let loading = $state(true)
  let loadError = $state(null)
  let selectedType = $state(null)
  let currentTime = $state(params?.selStart ?? 0)
  let isPlaying = $state(false)
  let duration = $state(0)
  let playTimer = null
  let sidebarCollapsed = $state({})

  // Current signal data from cache (instant on type switch)
  const signalData = $derived(allData && selectedType ? (allData[selectedType] ?? null) : null)

  // Category groupings
  const CATEGORIES = {
    'Control': ['carState', 'carControl', 'selfdriveState', 'controlsState', 'longitudinalPlan', 'lateralPlan', 'carOutput'],
    'Model': ['modelV2', 'driverMonitoringState', 'liveCalibration', 'livePose', 'liveParameters', 'liveTorqueParameters'],
    'Sensors': ['accelerometer', 'gyroscope', 'magnetometer', 'temperatureSensor', 'lightSensor'],
    'Navigation': ['gpsLocation', 'gpsLocationExternal', 'navInstruction', 'navRoute', 'navModel'],
    'System': ['initData', 'carParams', 'deviceState', 'peripheralState', 'pandaStates', 'managerState', 'sentinel', 'procLog'],
  }

  function categorize(msgTypes) {
    const groups = {}
    const assigned = new Set()
    for (const [cat, types] of Object.entries(CATEGORIES)) {
      const items = types.filter(t => msgTypes[t])
      if (items.length > 0) {
        groups[cat] = items
        items.forEach(t => assigned.add(t))
      }
    }
    const other = Object.keys(msgTypes).filter(t => !assigned.has(t)).sort()
    if (other.length > 0) groups['Other'] = other
    return groups
  }

  const groupedTypes = $derived(catalog ? categorize(catalog) : {})

  onMount(async () => {
    if (!routeId) return
    try {
      route = await fetchRoute(routeId)
      const maxSeg = route.maxqlog ?? 0
      duration = (maxSeg + 1) * 60

      // Scope to segments covering selection range
      const startSeg = Math.floor(selectionStart / 60)
      const endSeg = selectionEnd > 0
        ? Math.min(Math.floor(selectionEnd / 60), maxSeg)
        : maxSeg
      const segRange = startSeg === endSeg ? String(startSeg) : `${startSeg}-${endSeg}`

      // Single-pass load: catalog + all data at once
      const result = await fetchSignalAll(routeId, segRange)
      catalog = result.catalog
      allData = result.data
      loading = false

      // Auto-select first type with data
      if (catalog) {
        const first = Object.keys(catalog).sort()[0]
        if (first) selectedType = first
      }
    } catch (e) {
      loadError = e.message
      loading = false
    }
  })

  onDestroy(() => {
    if (playTimer) clearInterval(playTimer)
  })

  function selectType(msgType) {
    selectedType = msgType
  }

  function toggleCategory(cat) {
    sidebarCollapsed = { ...sidebarCollapsed, [cat]: !sidebarCollapsed[cat] }
  }

  function togglePlay() {
    if (isPlaying) {
      clearInterval(playTimer)
      playTimer = null
      isPlaying = false
    } else {
      isPlaying = true
      playTimer = setInterval(() => {
        currentTime += 0.2
        if (currentTime >= effectiveEnd) currentTime = selectionStart
      }, 200)
    }
  }

  function handleScrub(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    currentTime = selectionStart + pct * scrubRange
  }

  function handleKeydown(e) {
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      currentTime = Math.min(effectiveEnd, currentTime + (e.shiftKey ? 10 : 1))
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      currentTime = Math.max(selectionStart, currentTime - (e.shiftKey ? 10 : 1))
    } else if (e.key === ' ') {
      e.preventDefault()
      togglePlay()
    }
  }

  // Binary search for sample closest to time t
  function sampleAt(t) {
    const s = signalData
    if (!s || !s.length) return null
    let lo = 0, hi = s.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (s[mid].t < t) lo = mid + 1
      else hi = mid
    }
    if (lo > 0 && Math.abs(s[lo - 1].t - t) < Math.abs(s[lo].t - t)) lo--
    return s[lo]
  }

  const currentSample = $derived(signalData ? sampleAt(currentTime) : null)
  const currentTypeInfo = $derived(catalog?.[selectedType] ?? null)
  const isSnapshot = $derived(currentTypeInfo?.kind === 'snapshot')

  // ── JSON tree rendering helpers ──
  function getType(val) {
    if (val === null || val === undefined) return 'null'
    if (typeof val === 'boolean') return 'boolean'
    if (typeof val === 'number') return 'number'
    if (typeof val === 'string') return 'string'
    if (Array.isArray(val)) return 'array'
    if (typeof val === 'object') return 'object'
    return 'unknown'
  }

  function formatValue(val) {
    if (val === null || val === undefined) return 'null'
    if (typeof val === 'number') return Number.isInteger(val) ? String(val) : val.toFixed(3)
    if (typeof val === 'boolean') return String(val)
    if (typeof val === 'string') return `"${val}"`
    return String(val)
  }

  // Flatten nested dict into key-value pairs with dotted paths for numeric view
  function flattenFields(obj, prefix = '') {
    const entries = []
    if (!obj || typeof obj !== 'object') return entries
    for (const [key, val] of Object.entries(obj)) {
      if (key === 't') continue
      const path = prefix ? `${prefix}.${key}` : key
      const type = getType(val)
      if (type === 'object' && val !== null && !Array.isArray(val)) {
        entries.push(...flattenFields(val, path))
      } else if (type === 'array' && val.length > 0 && typeof val[0] === 'object') {
        val.forEach((item, i) => entries.push(...flattenFields(item, `${path}[${i}]`)))
      } else if (type === 'array') {
        entries.push({ path, val: `[${val.length}] ${val.slice(0, 5).map(v => formatValue(v)).join(', ')}${val.length > 5 ? '...' : ''}`, type: 'array' })
      } else {
        entries.push({ path, val: formatValue(val), type })
      }
    }
    return entries
  }

  const flatFields = $derived(currentSample ? flattenFields(currentSample) : [])
  const currentSegment = $derived(Math.floor(currentTime / 60))
  const effectiveEnd = $derived(selectionEnd > 0 ? selectionEnd : duration)
  const scrubRange = $derived(effectiveEnd - selectionStart)
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="h-dvh flex flex-col bg-surface-900 text-surface-100">
  <!-- Header -->
  <header class="flex items-center gap-3 py-2 px-4 border-b border-surface-700/50 bg-surface-800 shrink-0 mx-auto max-w-6xl w-full">
    <span class="text-sm text-surface-400">Logs & Signals</span>

    <!-- Time info: selStart | playhead | selEnd  Seg N — center aligned -->
    <div class="flex-1 flex items-center justify-center gap-3">
      <span class="text-xs font-mono tabular-nums text-surface-500">{formatVideoTime(selectionStart)}</span>
      <span class="text-xs font-mono tabular-nums text-surface-100 font-semibold">{formatVideoTime(currentTime)}</span>
      <span class="text-xs font-mono tabular-nums text-surface-500">{formatVideoTime(effectiveEnd)}</span>
      <span class="text-xs text-surface-400">Seg {currentSegment}</span>
    </div>

    <!-- Playback controls OR loading spinner (right-aligned) -->
    <div class="flex items-center gap-2">
      {#if loading}
        <div class="w-4 h-4 border-2 border-engage-green border-t-transparent rounded-full animate-spin"></div>
        <span class="text-xs text-surface-400">Loading signals...</span>
      {:else}
        <button class="w-6 h-6 flex items-center justify-center rounded hover:bg-surface-700 transition-colors" onclick={togglePlay}>
          {#if isPlaying}
            <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
          {:else}
            <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
          {/if}
        </button>
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="w-32 h-5 flex items-center cursor-pointer group" onclick={handleScrub}>
          <div class="w-full h-1 bg-surface-700 rounded-full relative">
            <div class="absolute inset-y-0 left-0 bg-engage-blue rounded-full" style="width: {scrubRange > 0 ? ((currentTime - selectionStart) / scrubRange) * 100 : 0}%"></div>
            <div class="absolute top-1/2 -translate-y-1/2 w-2 h-2 bg-white rounded-full shadow opacity-0 group-hover:opacity-100 transition-opacity" style="left: {scrubRange > 0 ? ((currentTime - selectionStart) / scrubRange) * 100 : 0}%"></div>
          </div>
        </div>
      {/if}
    </div>
  </header>

  <!-- Main content -->
  <div class="flex flex-1 min-h-0 mx-auto max-w-6xl w-full">
    <!-- Sidebar -->
    <aside class="w-60 shrink-0 border-r border-surface-700/50 overflow-y-auto bg-surface-850">
      {#if loading}
        <div class="flex items-center gap-2 justify-center py-8">
          <div class="w-4 h-4 border-2 border-engage-green border-t-transparent rounded-full animate-spin"></div>
          <span class="text-xs text-surface-400">Loading...</span>
        </div>
      {:else if loadError}
        <p class="text-xs text-red-400 p-3">{loadError}</p>
      {:else}
        {#each Object.entries(groupedTypes) as [category, types]}
          <div class="border-b border-surface-700/30">
            <button
              class="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold text-surface-500 uppercase tracking-wider hover:bg-surface-800"
              onclick={() => toggleCategory(category)}
            >
              {category}
              <svg class="w-3 h-3 transition-transform {sidebarCollapsed[category] ? '-rotate-90' : ''}" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            {#if !sidebarCollapsed[category]}
              {#each types as msgType}
                {@const info = catalog[msgType]}
                <button
                  class="w-full flex items-center justify-between px-3 py-1.5 text-left text-sm transition-colors
                    {selectedType === msgType ? 'bg-surface-700 border-l-2 border-engage-blue text-surface-100' : 'text-surface-300 hover:bg-surface-800 border-l-2 border-transparent'}"
                  onclick={() => selectType(msgType)}
                >
                  <span class="truncate">{msgType}</span>
                  <span class="text-xs text-surface-500 ml-1 shrink-0 tabular-nums">{info?.count ?? 0}</span>
                </button>
              {/each}
            {/if}
          </div>
        {/each}
      {/if}
    </aside>

    <!-- Content panel -->
    <main class="flex-1 overflow-y-auto min-w-0">
      {#if loading}
        <div class="flex items-center justify-center h-full">
          <p class="text-surface-500 text-sm">Loading signals...</p>
        </div>
      {:else if !selectedType}
        <div class="flex items-center justify-center h-full">
          <p class="text-surface-500 text-sm">Select a message type from the sidebar</p>
        </div>
      {:else if isSnapshot}
        <!-- Snapshot view: full JSON tree -->
        <div class="p-4">
          <div class="flex items-center gap-2 mb-3">
            <h2 class="text-sm font-semibold text-surface-200">{selectedType}</h2>
            <span class="text-xs px-1.5 py-0.5 bg-surface-700 rounded text-surface-400">snapshot</span>
            {#if currentTypeInfo}
              <span class="text-xs text-surface-500">{currentTypeInfo.count} occurrence{currentTypeInfo.count !== 1 ? 's' : ''}</span>
            {/if}
          </div>
          {#if currentSample}
            <div class="bg-surface-800 rounded-lg p-3 overflow-x-auto">
              <pre class="text-xs font-mono leading-relaxed whitespace-pre-wrap break-all"><code>{@html syntaxHighlight(currentSample)}</code></pre>
            </div>
          {:else}
            <p class="text-sm text-surface-500">No data at current time</p>
          {/if}
        </div>
      {:else}
        <!-- Numeric view: live value table -->
        <div class="p-4">
          <div class="flex items-center gap-2 mb-3">
            <h2 class="text-sm font-semibold text-surface-200">{selectedType}</h2>
            <span class="text-xs px-1.5 py-0.5 bg-surface-700 rounded text-surface-400">{currentTypeInfo?.freq_hz ?? 0} Hz</span>
            <span class="text-xs text-surface-500">{signalData?.length ?? 0} samples</span>
          </div>
          {#if flatFields.length > 0}
            <div class="bg-surface-800 rounded-lg overflow-hidden">
              <table class="w-full text-sm">
                <tbody>
                  {#each flatFields as field}
                    <tr class="border-b border-surface-700/30 last:border-0">
                      <td class="px-3 py-1.5 text-surface-400 font-mono text-xs whitespace-nowrap">{field.path}</td>
                      <td class="px-3 py-1.5 text-right font-mono text-xs whitespace-nowrap">
                        {#if field.type === 'boolean'}
                          <span class="inline-block w-2 h-2 rounded-full mr-1 {field.val === 'true' ? 'bg-engage-green' : 'bg-red-500'}"></span>
                          <span class="{field.val === 'true' ? 'text-engage-green' : 'text-red-400'}">{field.val}</span>
                        {:else if field.type === 'number'}
                          <span class="text-blue-400">{field.val}</span>
                        {:else if field.type === 'string'}
                          <span class="text-engage-green">{field.val}</span>
                        {:else if field.type === 'array'}
                          <span class="text-surface-400">{field.val}</span>
                        {:else}
                          <span class="text-surface-300">{field.val}</span>
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}
            <p class="text-sm text-surface-500">No data at current time</p>
          {/if}
        </div>
      {/if}
    </main>
  </div>

</div>

<script module>
  // JSON syntax highlighting for snapshot view
  function syntaxHighlight(obj) {
    const json = JSON.stringify(obj, null, 2)
    return json.replace(/("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g, (match) => {
      let cls = 'text-blue-400' // number
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = 'text-surface-400' // key
          match = match.replace(/:$/, '') + ':'
        } else {
          cls = 'text-engage-green' // string
        }
      } else if (/true|false/.test(match)) {
        cls = 'text-yellow-400' // boolean
      } else if (/null/.test(match)) {
        cls = 'text-surface-500' // null
      }
      return `<span class="${cls}">${match}</span>`
    })
  }
</script>

<style>
  aside {
    background-color: rgb(var(--color-surface-850, 18 18 22));
  }
</style>
