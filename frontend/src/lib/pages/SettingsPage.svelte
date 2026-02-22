<script>
  import { onMount } from 'svelte'
  import { Select } from 'bits-ui'
  import { fetchParams, setParam, fetchModels, swapModel, checkModelUpdates, downloadModel,
    fetchSoftware, softwareCheck, softwareDownload, softwareInstall, softwareBranch, softwareUninstall,
    fetchLateralDelay } from '../api.js'
  import { getTileSource, setTileSource, TILE_SOURCES } from '../tileSource.js'

  let params = $state({})
  let loading = $state(true)
  let error = $state(null)
  let saving = $state(null) // key currently being saved
  let latDelay = $state(null)
  let tileSource = $state(getTileSource())

  // Model state
  let models = $state(null)
  let modelsLoading = $state(true)
  let modelsError = $state(null)
  let swapping = $state(null) // model_id being swapped
  let swapResult = $state(null) // result message after swap
  let updates = $state(null) // available updates from check-updates
  let checking = $state(false)
  let downloading = $state(null) // model_id being downloaded
  let downloadPollTimer = $state(null)

  // Software update state
  let sw = $state(null)
  let swLoading = $state(true)
  let swError = $state(null)
  let swPollTimer = $state(null)
  let swExpanded = $state(false)

  const SECTIONS = [
    {
      title: 'Driving',
      items: [
        { key: 'DccCalibrationMode', label: 'DCC Calibration Mode', desc: 'Collect DCC response data for acceleration mapping', type: 'bool' },
        { key: 'LaneCenteringCorrection', label: 'Lane Centering Correction', desc: 'Apply learned steering offset for better centering', type: 'bool' },
      ],
    },
    {
      title: 'Speed Limits',
      items: [
        { key: 'MapdSpeedLimitControlEnabled', label: 'Map Speed Limit Control', desc: 'Automatically limit speed based on OSM map data', type: 'bool' },
        { key: 'MapdSpeedLimitOffsetPercent', label: 'Speed Limit Offset', desc: 'Percentage above the posted speed limit', type: 'int', options: [0, 5, 10, 15] },
      ],
    },
  ]

  // Derived: find the active model object from the list
  let activeDriving = $derived(models?.driving?.find(m => m.id === models?.active_driving))
  let activeDm = $derived(models?.dm?.find(m => m.id === models?.active_dm))

  onMount(async () => {
    try {
      params = await fetchParams()
      fetchLateralDelay().then(d => { latDelay = d }).catch(() => {})
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
    loadModels()
    loadSoftware()
  })

  async function loadModels() {
    modelsLoading = true
    modelsError = null
    try {
      models = await fetchModels()
      if (models.download?.status === 'downloading') {
        downloading = models.download.model_id
        startDownloadPoll()
      }
    } catch (e) {
      modelsError = e.message
    } finally {
      modelsLoading = false
    }
  }

  async function loadSoftware() {
    swLoading = true
    swError = null
    try {
      sw = await fetchSoftware()
      if (sw.UpdaterState && sw.UpdaterState !== 'idle') {
        startSwPoll()
      }
    } catch (e) {
      swError = e.message
    } finally {
      swLoading = false
    }
  }

  function startSwPoll() {
    if (swPollTimer) return
    swPollTimer = setInterval(async () => {
      try {
        sw = await fetchSoftware()
        if (!sw.UpdaterState || sw.UpdaterState === 'idle') {
          clearInterval(swPollTimer)
          swPollTimer = null
        }
      } catch { /* ignore */ }
    }, 2000)
  }

  async function handleSwCheck() {
    swError = null
    try {
      await softwareCheck()
      startSwPoll()
    } catch (e) {
      swError = e.message
    }
  }

  async function handleSwDownload() {
    swError = null
    try {
      await softwareDownload()
      startSwPoll()
    } catch (e) {
      swError = e.message
    }
  }

  async function handleSwInstall() {
    if (!confirm('Install update and reboot?')) return
    swError = null
    try {
      await softwareInstall()
    } catch (e) {
      swError = e.message
    }
  }

  async function handleSwBranch(branch) {
    if (!branch || branch === sw?.UpdaterTargetBranch) return
    swError = null
    try {
      await softwareBranch(branch)
      sw = { ...sw, UpdaterTargetBranch: branch }
      startSwPoll()
    } catch (e) {
      swError = e.message
    }
  }

  async function handleSwUninstall() {
    if (!confirm('This will remove openpilot. Are you sure?')) return
    if (!confirm('Really uninstall? This cannot be undone.')) return
    swError = null
    try {
      await softwareUninstall()
    } catch (e) {
      swError = e.message
    }
  }

  function lastCheckedAgo(lastTime) {
    if (!lastTime) return ''
    // Handle both ISO date strings and unix timestamps
    const ms = typeof lastTime === 'number' ? lastTime * 1000 : Date.parse(lastTime)
    if (isNaN(ms)) return ''
    const secs = Math.floor((Date.now() - ms) / 1000)
    if (secs < 60) return 'just now'
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
    return `${Math.floor(secs / 86400)}d ago`
  }

  async function toggle(key) {
    const prev = params[key]
    params[key] = !prev
    saving = key
    try {
      await setParam(key, params[key])
    } catch (e) {
      params[key] = prev
      error = e.message
    } finally {
      saving = null
    }
  }

  async function setOffset(key, value) {
    const prev = params[key]
    params[key] = value
    saving = key
    try {
      await setParam(key, value)
    } catch (e) {
      params[key] = prev
      error = e.message
    } finally {
      saving = null
    }
  }

  async function onDrivingSelect(modelId) {
    if (modelId === models.active_driving) return
    const model = models.driving.find(m => m.id === modelId)
    if (!model) return
    if (!confirm(`Switch to ${model.name}?\n\nA reboot will be required to activate the new model.`)) return
    swapping = modelId
    swapResult = null
    try {
      const result = await swapModel('driving', modelId)
      swapResult = result.error
        ? `Swap failed: ${result.error}`
        : `Switched to ${model.name}. Reboot required.`
      await loadModels()
    } catch (e) {
      swapResult = `Swap failed: ${e.message}`
    } finally {
      swapping = null
    }
  }

  async function onDmSelect(modelId) {
    if (modelId === models.active_dm) return
    const model = models.dm.find(m => m.id === modelId)
    if (!model) return
    if (!confirm(`Switch to ${model.name}?\n\nA reboot will be required to activate the new model.`)) return
    swapping = modelId
    swapResult = null
    try {
      const result = await swapModel('dm', modelId)
      swapResult = result.error
        ? `Swap failed: ${result.error}`
        : `Switched to ${model.name}. Reboot required.`
      await loadModels()
    } catch (e) {
      swapResult = `Swap failed: ${e.message}`
    } finally {
      swapping = null
    }
  }

  async function handleCheckUpdates() {
    checking = true
    modelsError = null
    try {
      updates = await checkModelUpdates()
    } catch (e) {
      modelsError = e.message
    } finally {
      checking = false
    }
  }

  function startDownloadPoll() {
    if (downloadPollTimer) return
    downloadPollTimer = setInterval(async () => {
      try {
        const fresh = await fetchModels()
        models = fresh
        if (!fresh.download || fresh.download.status !== 'downloading') {
          clearInterval(downloadPollTimer)
          downloadPollTimer = null
          downloading = null
          if (updates && fresh.download?.status === 'complete') {
            const doneId = fresh.download.model_id
            const doneType = fresh.download.type
            if (doneType === 'driving') {
              updates = { ...updates, driving: updates.driving.filter(m => m.id !== doneId), total: updates.total - 1 }
            } else {
              updates = { ...updates, dm: updates.dm.filter(m => m.id !== doneId), total: updates.total - 1 }
            }
          }
        }
      } catch {
        // ignore poll errors
      }
    }, 3000)
  }

  async function handleDownload(type, modelId) {
    downloading = modelId
    try {
      await downloadModel(type, modelId)
      startDownloadPoll()
    } catch (e) {
      modelsError = e.message
      downloading = null
    }
  }
</script>

<div class="max-w-lg mx-auto px-4 py-6 space-y-6">
  {#if loading}
    <div class="space-y-4">
      {#each [1, 2] as _}
        <div class="card p-4 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-24 mb-4"></div>
          <div class="h-10 bg-surface-700 rounded mb-3"></div>
          <div class="h-10 bg-surface-700 rounded"></div>
        </div>
      {/each}
    </div>
  {:else}
    {#if error}
      <div class="card p-4 border-engage-red/50">
        <p class="text-engage-red text-sm">{error}</p>
        <button class="btn-ghost text-xs mt-2" onclick={() => { error = null }}>Dismiss</button>
      </div>
    {/if}

    {#each SECTIONS as section}
      <div class="card p-4">
        <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider mb-4">{section.title}</h3>
        <div class="space-y-4">
          {#each section.items as item}
            {#if item.type === 'bool'}
              <button
                class="w-full flex items-center justify-between gap-4 group"
                onclick={() => toggle(item.key)}
                disabled={saving === item.key}
              >
                <div class="text-left">
                  <div class="text-sm text-surface-100">{item.label}</div>
                  <div class="text-xs text-surface-500 mt-0.5">{item.desc}</div>
                </div>
                <div
                  class="relative w-11 h-6 rounded-full shrink-0 transition-colors duration-200 {params[item.key] ? 'bg-engage-blue' : 'bg-surface-600'}"
                >
                  <div
                    class="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 {params[item.key] ? 'translate-x-5' : 'translate-x-0'}"
                  ></div>
                </div>
              </button>
            {:else if item.type === 'int'}
              <div>
                <div class="text-sm text-surface-100">{item.label}</div>
                <div class="text-xs text-surface-500 mt-0.5 mb-2">{item.desc}</div>
                <div class="flex gap-2">
                  {#each item.options as opt}
                    <button
                      class="px-3 py-1.5 text-sm rounded-lg transition-colors {params[item.key] === opt ? 'bg-engage-blue text-white' : 'bg-surface-700 text-surface-300 hover:bg-surface-600'}"
                      onclick={() => setOffset(item.key, opt)}
                      disabled={saving === item.key}
                    >
                      {opt}%
                    </button>
                  {/each}
                </div>
              </div>
            {/if}
          {/each}

          <!-- Lateral Delay (Driving section only) -->
          {#if section.title === 'Driving' && latDelay && !latDelay.error}
            <div class="pt-3 border-t border-surface-700">
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm text-surface-100">Lateral Delay</div>
                  <div class="text-xs text-surface-500 mt-0.5">Steering lag calibration</div>
                </div>
                <div class="text-right">
                  {#if latDelay.status === 'estimated'}
                    <div class="text-sm text-engage-green">{latDelay.lateralDelay.toFixed(3)}s</div>
                  {:else if latDelay.status === 'no data'}
                    <div class="text-sm text-surface-500">--</div>
                  {:else}
                    <div class="text-sm text-surface-300">{latDelay.lateralDelayEstimate.toFixed(3)}s</div>
                  {/if}
                  <div class="text-xs text-surface-500">{latDelay.calPerc ?? 0}%</div>
                </div>
              </div>
            </div>
          {/if}
        </div>
      </div>
    {/each}

    <!-- Map Tiles Source -->
    <div class="card p-4">
      <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider mb-4">Map Tiles</h3>
      <div class="flex gap-2">
        {#each Object.entries(TILE_SOURCES) as [key, src]}
          <button
            class="flex-1 rounded-lg px-3 py-2 text-left transition-colors {tileSource === key ? 'bg-engage-blue/15 border border-engage-blue/40' : 'bg-surface-700 border border-surface-600 hover:border-surface-500'}"
            onclick={() => { tileSource = key; setTileSource(key) }}
          >
            <div class="text-sm {tileSource === key ? 'text-engage-blue' : 'text-surface-100'}">{src.label}</div>
            <div class="text-xs text-surface-500 mt-0.5">{src.desc}</div>
          </button>
        {/each}
      </div>
      <div class="text-xs text-surface-500 mt-2">Reload route page to apply</div>
    </div>

    <!-- Software Section -->
    <div class="card p-4">
      <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider mb-4">Software</h3>

      {#if swLoading}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else if swError}
        <div class="text-engage-red text-sm mb-2">{swError}</div>
        <button class="btn-ghost text-xs" onclick={() => { swError = null; loadSoftware() }}>Retry</button>
      {:else if sw}
        <!-- Summary (always visible) -->
        <div class="text-sm text-surface-100">
          {sw.UpdaterCurrentDescription || `${sw.GitBranch} / ${sw.GitCommit?.slice(0, 7) || '???'}`}
        </div>

        <!-- Updater status -->
        {#if sw.UpdaterState && sw.UpdaterState !== 'idle'}
          <div class="flex items-center gap-2 text-sm text-surface-300 mt-2">
            <svg class="w-4 h-4 animate-spin text-engage-blue shrink-0" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="32 32" />
            </svg>
            {sw.UpdaterState}...
          </div>
        {:else if sw.UpdateFailedCount > 0}
          <div class="text-sm text-engage-red mt-2">Failed to check for update</div>
        {:else if sw.UpdaterFetchAvailable}
          <div class="mt-2">
            <button class="btn-primary text-sm px-4 py-1.5" onclick={handleSwDownload}>Download</button>
            <span class="text-xs text-surface-400 ml-2">update available</span>
          </div>
        {:else if sw.LastUpdateTime}
          <div class="text-xs text-surface-500 mt-1">up to date, checked {lastCheckedAgo(sw.LastUpdateTime)}</div>
        {/if}

        <!-- Install Update card -->
        {#if sw.UpdateAvailable}
          <div class="rounded-lg border border-engage-blue/30 bg-engage-blue/5 p-3 mt-3">
            <div class="text-xs text-surface-400 uppercase font-medium mb-1">Install Update</div>
            <div class="text-sm text-surface-200 mb-2">
              {sw.UpdaterNewDescription || 'New version ready'}
            </div>
            <button class="btn-primary text-sm px-4 py-1.5" onclick={handleSwInstall}>Install</button>
          </div>
        {/if}

        <!-- Expand/collapse details -->
        <button
          class="text-xs text-surface-500 hover:text-surface-300 transition-colors mt-3"
          onclick={() => { swExpanded = !swExpanded }}
        >
          {swExpanded ? 'Hide details' : 'Show details'}
        </button>

        {#if swExpanded}
          <div class="mt-3 space-y-3">
            <!-- Version details -->
            <div class="text-xs text-surface-500 space-y-1">
              <div>Branch: <span class="text-surface-300">{sw.GitBranch}</span></div>
              <div>Commit: <span class="text-surface-300">{sw.GitCommit?.slice(0, 7) || '???'}</span></div>
              {#if sw.GitCommitDate}
                <div>Date: <span class="text-surface-300">{sw.GitCommitDate}</span></div>
              {/if}
            </div>

            <!-- Branch selector (hidden if IsTestedBranch) -->
            {#if !sw.IsTestedBranch && sw.UpdaterAvailableBranches?.length > 0}
              <div>
                <div class="text-xs text-surface-500 uppercase font-medium mb-2">Target Branch</div>
                <Select.Root
                  type="single"
                  value={sw.UpdaterTargetBranch || sw.GitBranch}
                  onValueChange={handleSwBranch}
                  items={sw.UpdaterAvailableBranches.map(b => ({ value: b, label: b }))}
                >
                  <Select.Trigger
                    class="w-full flex items-center justify-between rounded-lg px-3 py-2.5 bg-surface-700 border border-surface-600 hover:border-surface-500 transition-colors text-left"
                  >
                    <span class="text-sm text-surface-100 truncate">{sw.UpdaterTargetBranch || sw.GitBranch}</span>
                    <svg class="w-4 h-4 text-surface-400 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                      <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                    </svg>
                  </Select.Trigger>
                  <Select.Content
                    class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto"
                    sideOffset={4}
                  >
                    {#each sw.UpdaterAvailableBranches as branch}
                      {@const isCurrent = branch === (sw.UpdaterTargetBranch || sw.GitBranch)}
                      <Select.Item
                        value={branch}
                        label={branch}
                        class="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors
                          data-[highlighted]:bg-surface-600 data-[selected]:text-engage-blue"
                      >
                        {#if isCurrent}
                          <div class="w-1.5 h-1.5 rounded-full bg-engage-blue shrink-0"></div>
                        {/if}
                        <span class="truncate">{branch}</span>
                      </Select.Item>
                    {/each}
                  </Select.Content>
                </Select.Root>
              </div>
            {/if}

            <!-- Action buttons (2 columns) -->
            <div class="pt-3 border-t border-surface-700 grid grid-cols-2 gap-2">
              <button
                class="btn-ghost text-sm justify-center py-2"
                onclick={handleSwCheck}
              >
                Update
              </button>
              <button
                class="text-sm py-2 rounded-lg text-engage-red/80 hover:text-engage-red hover:bg-engage-red/5 transition-colors"
                onclick={handleSwUninstall}
              >
                Uninstall
              </button>
            </div>
          </div>
        {/if}
      {/if}
    </div>

    <!-- Models Section -->
    <div class="card p-4">
      <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider mb-4">Models</h3>

      {#if modelsLoading}
        <div class="space-y-3 animate-pulse">
          <div class="h-10 bg-surface-700 rounded-lg"></div>
          <div class="h-10 bg-surface-700 rounded-lg"></div>
        </div>
      {:else if modelsError}
        <div class="text-engage-red text-sm mb-2">{modelsError}</div>
        <button class="btn-ghost text-xs" onclick={() => { modelsError = null; loadModels() }}>Retry</button>
      {:else if models}
        {#if swapResult}
          <div class="rounded-lg px-3 py-2 text-sm mb-3 {swapResult.includes('failed') ? 'bg-engage-red/10 text-engage-red' : 'bg-engage-green/10 text-engage-green'}">
            {swapResult}
            <button class="ml-2 opacity-60 hover:opacity-100" onclick={() => { swapResult = null }}>x</button>
          </div>
        {/if}

        <!-- Driving Model Select -->
        <div class="space-y-3">
          <div class="relative">
            <Select.Root
              type="single"
              value={models.active_driving}
              onValueChange={onDrivingSelect}
              items={models.driving.map(m => ({ value: m.id, label: m.name }))}
            >
              <Select.Trigger
                class="w-full flex items-center justify-between rounded-lg px-3 py-2.5 bg-surface-700 border border-surface-600 hover:border-surface-500 transition-colors text-left"
              >
                <div class="flex items-center gap-2 min-w-0">
                  <span class="text-xs text-surface-400 font-medium uppercase shrink-0">Driving</span>
                  <span class="text-sm text-surface-100 truncate">{activeDriving?.name ?? '...'}</span>
                  {#if activeDriving?.date}
                    <span class="text-xs text-surface-500 shrink-0">{activeDriving.date}</span>
                  {/if}
                </div>
                {#if swapping}
                  <svg class="w-4 h-4 animate-spin text-surface-400 shrink-0" viewBox="0 0 24 24" fill="none">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="32 32" />
                  </svg>
                {:else}
                  <svg class="w-4 h-4 text-surface-400 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                  </svg>
                {/if}
              </Select.Trigger>
              <Select.Content
                class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto"
                sideOffset={4}
              >
                {#each models.driving as model}
                  {@const isActive = model.id === models.active_driving}
                  <Select.Item
                    value={model.id}
                    label={model.name}
                    class="flex items-center justify-between px-3 py-2 text-sm cursor-pointer transition-colors
                      data-[highlighted]:bg-surface-600 data-[selected]:text-engage-blue"
                  >
                    <div class="flex items-center gap-2 min-w-0">
                      {#if isActive}
                        <div class="w-1.5 h-1.5 rounded-full bg-engage-blue shrink-0"></div>
                      {/if}
                      <span class="truncate">{model.name}</span>
                    </div>
                    <div class="flex items-center gap-2 shrink-0 ml-2">
                      {#if model.has_pkl}
                        <span class="text-[10px] text-engage-orange px-1 py-0.5 bg-engage-orange/10 rounded">cached</span>
                      {/if}
                      {#if model.date}
                        <span class="text-xs text-surface-500">{model.date}</span>
                      {/if}
                    </div>
                  </Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>

          <!-- DM Model Select -->
          {#if models.dm.length > 0}
            <div class="relative">
              <Select.Root
                type="single"
                value={models.active_dm}
                onValueChange={onDmSelect}
                items={models.dm.map(m => ({ value: m.id, label: m.name }))}
              >
                <Select.Trigger
                  class="w-full flex items-center justify-between rounded-lg px-3 py-2.5 bg-surface-700 border border-surface-600 hover:border-surface-500 transition-colors text-left"
                >
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-xs text-surface-400 font-medium uppercase shrink-0">DM</span>
                    <span class="text-sm text-surface-100 truncate">{activeDm?.name ?? '...'}</span>
                    {#if activeDm?.date}
                      <span class="text-xs text-surface-500 shrink-0">{activeDm.date}</span>
                    {/if}
                  </div>
                  <svg class="w-4 h-4 text-surface-400 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                  </svg>
                </Select.Trigger>
                <Select.Content
                  class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto"
                  sideOffset={4}
                >
                  {#each models.dm as model}
                    {@const isActive = model.id === models.active_dm}
                    <Select.Item
                      value={model.id}
                      label={model.name}
                      class="flex items-center justify-between px-3 py-2 text-sm cursor-pointer transition-colors
                        data-[highlighted]:bg-surface-600 data-[selected]:text-engage-blue"
                    >
                      <div class="flex items-center gap-2 min-w-0">
                        {#if isActive}
                          <div class="w-1.5 h-1.5 rounded-full bg-engage-blue shrink-0"></div>
                        {/if}
                        <span class="truncate">{model.name}</span>
                      </div>
                      <div class="flex items-center gap-2 shrink-0 ml-2">
                        {#if model.has_pkl}
                          <span class="text-[10px] text-engage-orange px-1 py-0.5 bg-engage-orange/10 rounded">cached</span>
                        {/if}
                        {#if model.date}
                          <span class="text-xs text-surface-500">{model.date}</span>
                        {/if}
                      </div>
                    </Select.Item>
                  {/each}
                </Select.Content>
              </Select.Root>
            </div>
          {/if}
        </div>

        <!-- Updates -->
        <div class="pt-3 mt-3 border-t border-surface-700">
          <button
            class="btn-ghost text-sm w-full justify-center"
            onclick={handleCheckUpdates}
            disabled={checking}
          >
            {#if checking}
              <svg class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="32 32" />
              </svg>
              Checking...
            {:else}
              Check for Updates
            {/if}
          </button>

          {#if updates}
            {#if updates.total === 0}
              <p class="text-xs text-surface-500 text-center mt-2">All models up to date</p>
            {:else}
              <p class="text-xs text-surface-400 text-center mt-2">{updates.total} new model{updates.total === 1 ? '' : 's'} available</p>

              {#if updates.driving?.length > 0}
                <div class="mt-3 space-y-2">
                  {#each updates.driving as model}
                    <div class="flex items-center justify-between rounded-lg p-2.5 bg-surface-700/50">
                      <div class="min-w-0">
                        <div class="text-sm text-surface-200 truncate">{model.name}</div>
                        <div class="text-xs text-surface-500">{model.date}</div>
                      </div>
                      <button
                        class="btn-primary text-xs px-3 py-1 shrink-0 ml-2"
                        onclick={() => handleDownload('driving', model.id)}
                        disabled={downloading != null}
                      >
                        {#if downloading === model.id}
                          <svg class="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="32 32" />
                          </svg>
                        {:else}
                          Download
                        {/if}
                      </button>
                    </div>
                  {/each}
                </div>
              {/if}

              {#if updates.dm?.length > 0}
                <div class="mt-3 space-y-2">
                  <div class="text-xs text-surface-500 font-medium">DM Models</div>
                  {#each updates.dm as model}
                    <div class="flex items-center justify-between rounded-lg p-2.5 bg-surface-700/50">
                      <div class="min-w-0">
                        <div class="text-sm text-surface-200 truncate">{model.name}</div>
                        <div class="text-xs text-surface-500">{model.date}</div>
                      </div>
                      <button
                        class="btn-primary text-xs px-3 py-1 shrink-0 ml-2"
                        onclick={() => handleDownload('dm', model.id)}
                        disabled={downloading != null}
                      >
                        {#if downloading === model.id}
                          <svg class="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="32 32" />
                          </svg>
                        {:else}
                          Download
                        {/if}
                      </button>
                    </div>
                  {/each}
                </div>
              {/if}
            {/if}
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>
