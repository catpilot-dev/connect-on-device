<script>
  import { onMount, onDestroy } from 'svelte'
  import { Select } from 'bits-ui'
  import CollapsibleCard from '../components/CollapsibleCard.svelte'
  import Spinner from '../components/Spinner.svelte'
  import ChevronIcon from '../components/ChevronIcon.svelte'
  import Toggle from '../components/Toggle.svelte'
  import { createPoll } from '../utils/poll.js'
  import { fetchParams, setParam,
    fetchSoftware, softwareCheck, softwareDownload, softwareInstall, softwareBranch, softwareUninstall,
    softwarePreparePlugins,
    fetchLateralDelay, fetchDeviceInfo, deviceReboot, devicePoweroff, deviceSetLanguage,
    fetchToggles, setToggle, fetchStorage, mapdCheckUpdate, mapdUpdate,
    fetchSshKeys, setSshKeys, removeSshKeys, fetchTileList } from '../api.js'
  import { getTileSource, setTileSource, TILE_SOURCES } from '../tileSource.js'
  import { formatBytes } from '../format.js'
  import { storageInfo } from '../stores.js'

  let { isOnroad = false } = $props()
  let params = $state({})
  let loading = $state(true)
  let error = $state(null)
  let latDelay = $state(null)
  let tileSource = $state(getTileSource())

  // Panel expanded state
  let devExpanded = $state(false)
  let togglesExpanded = $state(false)
  let devTogglesExpanded = $state(false)
  let sectionExpanded = $state({ 'Driving': true })
  let mapdExpanded = $state(false)
  let swExpanded = $state(false)

  // Software update state
  let sw = $state(null)
  let swLoading = $state(true)
  let swError = $state(null)
  let swChecking = $state(false)
  let swInstallPhase = $state(null)  // null | 'downloading' | 'preparing' | 'installing' | 'rebooting'
  let swChecked = $state(false)
  const swRepoUrl = $derived(sw?.GitRemote?.replace(/\.git$/, '') || null)

  // Device state
  let dev = $state(null)
  let storage = $state(null)

  // Toggles state
  let toggles = $state(null)
  let toggling = $state(null)
  let sshKeys = $state(null)
  let sshLoading = $state(false)
  let sshError = $state(null)

  // Mapd update state
  let mapdVersion = $state(null)
  let mapdLatest = $state(null)
  let mapdReleaseDate = $state(null)
  let mapdChecking = $state(false)
  let mapdUpdating = $state(false)
  let mapdError = $state(null)
  let tileStorage = $state(null)  // {tile_count, total_mb}

  // Poll timers (cleaned up on destroy)
  const swPoll = createPoll(async () => {
    try {
      sw = await fetchSoftware()
      if (!sw.UpdaterState || sw.UpdaterState === 'idle') {
        swPoll.stop()
        if (swChecking) { swChecking = false; swChecked = true }
      }
    } catch { /* ignore */ }
  }, 2000)

  onDestroy(() => { swPoll.stop() })

  const SECTIONS = [
    {
      title: 'Driving',
      items: [],
    },
  ]

  let storagePct = $derived(storage ? Math.round(100 - storage.percent_free) : 0)
  let storageBarColor = $derived(
    storagePct >= 80 ? 'bg-red-500' :
    storagePct >= 60 ? 'bg-amber-400' :
    'bg-green-500'
  )

  onMount(async () => {
    fetchLateralDelay().then(d => { latDelay = d }).catch(() => {})
    fetchSoftware().then(d => { sw = d; swLoading = false }).catch(() => { swLoading = false })
    try {
      params = await fetchParams()
      if (params.MapdVersion) mapdVersion = params.MapdVersion
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  })

  function onDevOpen(open) {
    if (open && !dev) {
      fetchDeviceInfo().then(d => { dev = d }).catch(() => {})
      fetchStorage().then(s => { storage = s; storageInfo.set(s) }).catch(() => {})
    }
  }

  function onTogglesOpen(open) {
    if (open && !toggles) {
      fetchToggles().then(t => { toggles = t }).catch(() => {})
    }
  }

  function onDevTogglesOpen(open) {
    if (open && !toggles) {
      fetchToggles().then(t => { toggles = t }).catch(() => {})
    }
    if (open && !sshKeys) {
      fetchSshKeys().then(s => { sshKeys = s }).catch(() => {})
    }
  }

  function onSwOpen(open) {
    if (open && !sw) loadSoftware()
    if (open && sw && !swChecked && !swChecking && !swInstallPhase && !sw?.UpdateAvailable) handleSwAutoCheck()
  }

  function onMapdOpen(open) {
    if (open && !mapdLatest && !mapdChecking) handleMapdCheck()
    if (open && !tileStorage) {
      fetchTileList().then(d => { tileStorage = d.storage }).catch(() => {})
    }
  }

  async function loadSoftware() {
    swLoading = true
    swError = null
    try {
      sw = await fetchSoftware()
      // Reflect existing state on load
      if (sw.UpdateAvailable) swChecked = true
      if (sw.UpdaterFetchAvailable) swChecked = true
      if (sw.UpdaterState && sw.UpdaterState !== 'idle') {
        if (sw.UpdaterState === 'checking') swChecking = true
        swPoll.start()
      }
    } catch (e) {
      swError = e.message
    } finally {
      swLoading = false
    }
  }

  async function handleSwAutoCheck() {
    if (swChecking || swInstallPhase) return
    swChecking = true
    swChecked = false
    swError = null
    try {
      await softwareCheck()
      swPoll.start()
    } catch (e) {
      swError = e.message
      swChecking = false
    }
  }

  async function handleSwInstall() {
    swError = null
    try {
      // Already downloaded — skip to install
      if (sw.UpdateAvailable) {
        // Prepare plugins from staged update before installing
        swInstallPhase = 'preparing'
        try { await softwarePreparePlugins() } catch { /* no plugins in branch — fine */ }
        swInstallPhase = 'installing'
        await softwareInstall()
        swInstallPhase = 'rebooting'
        waitForReboot()
        return
      }
      // Download first
      swInstallPhase = 'downloading'
      await softwareDownload()
      const installPoll = createPoll(async () => {
        try {
          sw = await fetchSoftware()
          if (sw.UpdateAvailable) {
            installPoll.stop()
            // Prepare plugins from staged update before installing
            swInstallPhase = 'preparing'
            try { await softwarePreparePlugins() } catch { /* no plugins in branch — fine */ }
            swInstallPhase = 'installing'
            await softwareInstall()
            swInstallPhase = 'rebooting'
            waitForReboot()
          } else if (!sw.UpdaterState || sw.UpdaterState === 'idle') {
            installPoll.stop()
            swInstallPhase = null
            if (sw.UpdateFailedCount > 0) swError = 'Update failed'
          }
        } catch { /* ignore poll errors */ }
      }, 2000)
      installPoll.start()
    } catch (e) {
      swError = e.message
      swInstallPhase = null
    }
  }

  function waitForReboot() {
    // Poll until server comes back after reboot, then refresh state
    const rebootPoll = setInterval(async () => {
      try {
        const fresh = await fetchSoftware()
        // Server is back — update complete
        clearInterval(rebootPoll)
        sw = fresh
        swInstallPhase = null
        swChecked = true
      } catch { /* still rebooting */ }
    }, 3000)
  }

  async function handleSwBranch(branch) {
    if (!branch || branch === sw?.UpdaterTargetBranch) return
    swError = null
    swChecked = false
    try {
      await softwareBranch(branch)
      sw = { ...sw, UpdaterTargetBranch: branch }
      // Auto-check after branch change
      swChecking = true
      await softwareCheck()
      swPoll.start()
    } catch (e) {
      swError = e.message
      swChecking = false
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

  const TOGGLE_DEFS = [
    { key: 'OpenpilotEnabledToggle', label: 'Enable openpilot' },
    { key: 'ExperimentalMode', label: 'Experimental Mode' },
    { key: 'DisengageOnAccelerator', label: 'Disengage on Accelerator Pedal' },
    { key: 'IsLdwEnabled', label: 'Lane Departure Warnings' },
    { key: 'AlwaysOnDM', label: 'Always-On Driver Monitoring' },
    { key: 'RecordFront', label: 'Record Driver Camera' },
    { key: 'RecordAudio', label: 'Record Audio' },
    { key: 'IsMetric', label: 'Use Metric System' },
  ]

  const DEV_DEFS = [
    { key: 'AdbEnabled', label: 'Enable ADB' },
    { key: 'SshEnabled', label: 'Enable SSH' },
    { key: 'JoystickDebugMode', label: 'Joystick Debug Mode' },
    { key: 'LongitudinalManeuverMode', label: 'Longitudinal Maneuver Mode' },
    { key: 'AlphaLongitudinalEnabled', label: 'openpilot Longitudinal Control (Alpha)' },
  ]

  // Mutual exclusion: toggling one on turns the other off
  const TOGGLE_MUTEX = {
    JoystickDebugMode: 'LongitudinalManeuverMode',
    LongitudinalManeuverMode: 'JoystickDebugMode',
  }

  const PERSONALITIES = [
    { value: 0, label: 'Aggressive' },
    { value: 1, label: 'Standard' },
    { value: 2, label: 'Relaxed' },
  ]

  async function handleToggle(key) {
    if (toggling) return
    const prev = toggles[key]
    const newVal = !prev
    toggles[key] = newVal
    // Mutual exclusion: turning one on turns the other off
    const mutex = TOGGLE_MUTEX[key]
    if (newVal && mutex) toggles[mutex] = false
    toggling = key
    try {
      await setToggle(key, newVal)
    } catch (e) {
      toggles[key] = prev
      if (mutex) toggles[mutex] = !prev ? false : toggles[mutex]
      error = e.message
    } finally {
      toggling = null
    }
  }

  async function handlePersonality(value) {
    if (value === params?.LongitudinalPersonality) return
    const prev = params.LongitudinalPersonality
    params.LongitudinalPersonality = value
    try {
      await setParam('LongitudinalPersonality', value)
    } catch (e) {
      params.LongitudinalPersonality = prev
      error = e.message
    }
  }

  const LANGUAGES = [
    { value: 'main_en', label: 'English' },
    { value: 'main_zh-CHS', label: '简体中文' },
    { value: 'main_zh-CHT', label: '繁體中文' },
    { value: 'main_ja', label: '日本語' },
    { value: 'main_ko', label: '한국어' },
    { value: 'main_de', label: 'Deutsch' },
    { value: 'main_fr', label: 'Français' },
    { value: 'main_es', label: 'Español' },
    { value: 'main_pt-BR', label: 'Português' },
    { value: 'main_ar', label: 'العربية' },
    { value: 'main_tr', label: 'Türkçe' },
    { value: 'main_nl', label: 'Nederlands' },
    { value: 'main_pl', label: 'Polski' },
    { value: 'main_th', label: 'ภาษาไทย' },
  ]

  async function handleReboot() {
    if (!confirm('Reboot device?')) return
    try { await deviceReboot() } catch (e) { error = e.message }
  }

  async function handlePoweroff() {
    if (!confirm('Power off device?')) return
    try { await devicePoweroff() } catch (e) { error = e.message }
  }

  async function handleLanguage(lang) {
    if (!lang || lang === dev?.LanguageSetting) return
    try {
      await deviceSetLanguage(lang)
      dev = { ...dev, LanguageSetting: lang }
    } catch (e) { error = e.message }
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

  async function handleMapdCheck() {
    mapdChecking = true
    mapdError = null
    try {
      const res = await mapdCheckUpdate()
      if (res.error) {
        mapdError = res.error
      } else {
        mapdVersion = res.current
        mapdLatest = res.latest
        if (res.release_date) mapdReleaseDate = res.release_date
      }
    } catch (e) {
      mapdError = e.message
    } finally {
      mapdChecking = false
    }
  }

  async function handleMapdUpdate() {
    if (!confirm(`Update mapd from ${mapdVersion} to ${mapdLatest}?`)) return
    mapdUpdating = true
    mapdError = null
    try {
      const res = await mapdUpdate()
      if (res.error) {
        mapdError = res.error
      } else {
        mapdVersion = res.version || mapdLatest
        mapdLatest = mapdVersion
      }
    } catch (e) {
      mapdError = e.message
    } finally {
      mapdUpdating = false
    }
  }
</script>

<div class="w-full max-w-lg mx-auto px-4 py-6 space-y-6 overflow-hidden">
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
      <CollapsibleCard title={section.title} bind:open={sectionExpanded[section.title]}>
        <div class="space-y-4">
          {#if section.title === 'Driving' && 'LongitudinalPersonality' in params}
            <div class="flex items-center gap-2">
              <div class="text-sm text-surface-100">Personality</div>
              <div class="flex-1"></div>
              {#each PERSONALITIES as p}
                <button
                  class="px-2.5 py-1 text-xs rounded-full transition-colors {params.LongitudinalPersonality === p.value ? 'bg-engage-blue/20 text-engage-blue border border-engage-blue/40' : 'bg-surface-700 text-surface-300 border border-surface-600 hover:border-surface-500'}"
                  onclick={() => handlePersonality(p.value)}
                >
                  {p.label}
                </button>
              {/each}
            </div>
          {/if}
          {#each section.items.filter(i => i.key in params) as item}
            {#if item.type === 'bool'}
              <div class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm text-surface-100">{item.label}</div>
                  <div class="text-xs text-surface-500 mt-0.5">{item.desc}</div>
                </div>
                <Toggle
                  checked={params[item.key]}
                  disabled={saving === item.key}
                  label={item.label}
                  onCheckedChange={() => toggle(item.key)}
                />
              </div>
            {:else if item.type === 'pills'}
              {@const disabled = item.dependsOn ? !params[item.dependsOn] : false}
              <div class="{disabled ? 'opacity-40 pointer-events-none' : ''}">
                <div class="flex items-center gap-2">
                  <div class="text-sm text-surface-100">{item.label}</div>
                  <div class="flex-1"></div>
                {#each item.options as opt, i}
                  {@const val = String(typeof opt === 'string' ? i : opt)}
                  {@const active = String(params[item.key]) === val}
                  <button
                    class="px-2.5 py-1 text-xs rounded-full transition-colors {active ? 'bg-engage-blue/20 text-engage-blue border border-engage-blue/40' : 'bg-surface-700 text-surface-300 border border-surface-600 hover:border-surface-500'}"
                    {disabled}
                    onclick={() => setOffset(item.key, typeof opt === 'string' ? i : opt)}
                  >
                    {opt}{item.suffix || ''}
                  </button>
                {/each}
                </div>
                {#if item.desc}<div class="text-xs text-surface-500 mt-0.5">{item.desc}</div>{/if}
              </div>
            {/if}
          {/each}
          {#if section.title === 'Driving' && latDelay && !latDelay.error}
            <div class="pt-3 ">
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
      </CollapsibleCard>
    {/each}

    <!-- Mapd & Maps -->
    {#if !isOnroad}
    <CollapsibleCard
      title="Mapd & Maps"
      metadata={mapdVersion || ''}
      bind:open={mapdExpanded}
      onOpenChange={onMapdOpen}
    >
      <div class="space-y-4">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="text-sm text-surface-100">
              mapd {mapdVersion || '...'}
              {#if mapdReleaseDate}
                <span class="text-surface-500 text-xs">({mapdReleaseDate})</span>
              {/if}
              {#if mapdLatest && mapdLatest !== mapdVersion}
                <span class="text-surface-500">&rarr;</span>
                <span class="text-engage-green">{mapdLatest}</span>
              {/if}
            </div>
            {#if mapdError}
              <div class="text-xs text-engage-red mt-0.5">{mapdError}</div>
            {/if}
          </div>
          <div class="shrink-0">
            {#if mapdUpdating}
              <div class="flex items-center gap-2 text-xs text-surface-400">
                <Spinner />
                Installing...
              </div>
            {:else if mapdChecking}
              <div class="flex items-center gap-2 text-xs text-surface-400">
                <Spinner />
                Checking
              </div>
            {:else if mapdLatest && mapdLatest !== mapdVersion}
              <button
                class="text-xs px-3 py-1.5 rounded-lg bg-engage-green/15 text-engage-green hover:bg-engage-green/25 transition-colors"
                onclick={handleMapdUpdate}
              >
                Install Update
              </button>
            {:else if mapdLatest}
              <span class="text-xs px-3 py-1.5 text-surface-500">Up to Date</span>
            {:else}
              <button
                class="text-xs px-3 py-1.5 rounded-lg bg-surface-700 text-surface-300 hover:bg-surface-600 transition-colors"
                onclick={handleMapdCheck}
              >Retry</button>
            {/if}
          </div>
        </div>
        <div class="pt-3 ">
          <button class="w-full flex items-center justify-between group" onclick={() => window.open('/tiles', 'tiles', 'width=720,height=500')}>
            <div class="text-left">
              <div class="text-sm text-surface-100">Map Tiles Management</div>
              <div class="text-xs text-surface-500 mt-0.5">{#if tileStorage}{tileStorage.tile_count} tiles &middot; {tileStorage.total_mb} MB{:else}Download and manage OSM offline tiles{/if}</div>
            </div>
            <svg class="w-5 h-5 text-surface-500 group-hover:text-surface-300 transition-colors" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clip-rule="evenodd" />
            </svg>
          </button>
        </div>
        <div class="pt-3  flex items-center gap-2">
          <div class="text-sm text-surface-100">Tile Source</div>
          <div class="flex-1"></div>
          {#each Object.entries(TILE_SOURCES) as [key, src]}
            <button
              class="px-2.5 py-1 text-xs rounded-full transition-colors {tileSource === key ? 'bg-engage-blue/20 text-engage-blue border border-engage-blue/40' : 'bg-surface-700 text-surface-300 border border-surface-600 hover:border-surface-500'}"
              onclick={() => { tileSource = key; setTileSource(key) }}
            >
              {src.label}
            </button>
          {/each}
        </div>
      </div>
    </CollapsibleCard>
    {/if}

    {#if !isOnroad}
    <!-- Software Section (collapsed by default) -->
    <CollapsibleCard
      title="Software"
      metadata={sw?.GitBranch || ''}
      bind:open={swExpanded}
      onOpenChange={onSwOpen}
    >
      {#if swLoading}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else if sw}
        <div class="space-y-4">
          {#if sw.UpdaterCurrentDescription}
            {@const curParts = sw.UpdaterCurrentDescription.split(' / ')}
            {@const curCommitShort = curParts.length >= 3 ? curParts[2] : null}
            <div class="text-sm text-surface-100">
              {curParts[0]}{curParts.length > 1 ? ` / ${curParts[1]}` : ''}
              {#if curCommitShort && swRepoUrl && sw.GitCommit}
                / <a href="{swRepoUrl}/commit/{sw.GitCommit}" target="_blank" rel="noopener" class="text-engage-blue hover:underline">{curCommitShort}</a>
              {:else if curCommitShort}
                / {curCommitShort}
              {/if}
              {#if curParts.length > 3} / {curParts.slice(3).join(' / ')}{/if}
            </div>
          {/if}
          {#if (sw.UpdaterFetchAvailable || sw.UpdateAvailable) && sw.UpdaterNewDescription}
            <div class="text-sm text-engage-green">Update: {sw.UpdaterNewDescription}</div>
          {/if}
          {#if sw.UpdaterWarning}
            <div class="text-xs text-amber-400">{sw.UpdaterWarning}</div>
          {/if}
          {#if swError}
            <div class="text-xs text-engage-red">{swError}</div>
          {/if}
          {#if !sw.IsTestedBranch && sw.UpdaterAvailableBranches?.length > 0}
            <div class="pt-3 ">
              <div class="text-sm text-surface-100 mb-2">Target Branch</div>
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
                  <ChevronIcon class="text-surface-400 shrink-0" />
                </Select.Trigger>
                <Select.Content
                  class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto max-w-[calc(100vw-2rem)]"
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
          <div class="pt-3  grid grid-cols-2 gap-2">
            {#if swInstallPhase}
              <button class="text-sm py-2 rounded-lg bg-surface-700 text-surface-400 flex items-center justify-center gap-2" disabled>
                <Spinner />
                {swInstallPhase === 'downloading' ? 'Downloading' : swInstallPhase === 'preparing' ? 'Preparing plugins' : swInstallPhase === 'installing' ? 'Installing' : swInstallPhase === 'rebooting' ? 'Rebooting' : 'Installing'}
              </button>
            {:else if swChecking}
              <button class="text-sm py-2 rounded-lg bg-surface-700 text-surface-400 flex items-center justify-center gap-2" disabled>
                <Spinner />
                Checking
              </button>
            {:else if sw.UpdateAvailable || sw.UpdaterFetchAvailable}
              <button
                class="text-sm py-2 rounded-lg bg-engage-green/15 text-engage-green hover:bg-engage-green/25 transition-colors"
                onclick={handleSwInstall}
              >
                Install
              </button>
            {:else if sw.UpdateFailedCount > 0}
              <button
                class="text-sm py-2 rounded-lg bg-surface-700 text-surface-300 hover:bg-surface-600 transition-colors"
                onclick={handleSwAutoCheck}
              >
                Retry
              </button>
            {:else if swChecked}
              <button class="text-sm py-2 rounded-lg bg-surface-700 text-surface-500" disabled>
                Up to Date
              </button>
            {:else}
              <button
                class="text-sm py-2 rounded-lg bg-surface-700 text-surface-300 hover:bg-surface-600 transition-colors"
                onclick={handleSwAutoCheck}
              >
                Check
              </button>
            {/if}
            <button
              class="text-sm py-2 rounded-lg bg-engage-red/15 text-engage-red hover:bg-engage-red/25 transition-colors"
              onclick={handleSwUninstall}
            >
              Uninstall
            </button>
          </div>
        </div>
      {/if}
    </CollapsibleCard>
    {/if}

    <!-- Toggles Section (collapsed by default, lazy loaded) -->
    <CollapsibleCard title="Toggles" bind:open={togglesExpanded} onOpenChange={onTogglesOpen}>
      {#if !toggles}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else}
        <div class="space-y-4">
          {#each TOGGLE_DEFS as t}
            <div class="flex items-center justify-between gap-3">
              <div class="text-sm text-surface-100">{t.label}</div>
              <Toggle
                checked={toggles[t.key]}
                disabled={toggling === t.key}
                label={t.label}
                onCheckedChange={() => handleToggle(t.key)}
              />
            </div>
          {/each}
        </div>
      {/if}
    </CollapsibleCard>

    <!-- Device Section (collapsed by default, lazy loaded) -->
    <CollapsibleCard title="Device" metadata={dev?.DongleId || ''} bind:open={devExpanded} onOpenChange={onDevOpen}>
      {#if !dev}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else}
        <div class="space-y-3">
          <div class="flex items-center justify-between">
            <span class="text-sm text-surface-400">Dongle ID</span>
            <span class="text-sm text-surface-100 font-mono">{dev.DongleId || '--'}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-sm text-surface-400">Serial</span>
            <span class="text-sm text-surface-100 font-mono">{dev.HardwareSerial || '--'}</span>
          </div>
          {#if storage}
            <div class="flex items-center justify-between gap-3">
              <span class="text-sm text-surface-400">Storage</span>
              <div class="flex items-center gap-2">
                <div class="w-24 h-1.5 rounded-full bg-surface-700 overflow-hidden">
                  <div
                    class="h-full rounded-full transition-all duration-500 {storageBarColor}"
                    style="width: {storagePct}%"
                  ></div>
                </div>
                <span class="text-xs text-surface-400 whitespace-nowrap">
                  {formatBytes(storage.used)} / {formatBytes(storage.total)}
                </span>
              </div>
            </div>
          {/if}
          <div class="flex items-center justify-between">
            <span class="text-sm text-surface-400">Language</span>
            <Select.Root
              type="single"
              value={dev.LanguageSetting || 'main_en'}
              onValueChange={handleLanguage}
              items={LANGUAGES}
            >
              <Select.Trigger
                class="flex items-center gap-2 rounded-lg px-3 py-1.5 bg-surface-700 border border-surface-600 hover:border-surface-500 transition-colors"
              >
                <span class="text-sm text-surface-100">{LANGUAGES.find(l => l.value === (dev.LanguageSetting || 'main_en'))?.label || dev.LanguageSetting}</span>
                <ChevronIcon class="!w-3.5 !h-3.5 text-surface-400" />
              </Select.Trigger>
              <Select.Content
                class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto max-w-[calc(100vw-2rem)]"
                sideOffset={4}
              >
                {#each LANGUAGES as lang}
                  <Select.Item
                    value={lang.value}
                    label={lang.label}
                    class="px-3 py-2 text-sm cursor-pointer transition-colors
                      data-[highlighted]:bg-surface-600 data-[selected]:text-engage-blue"
                  >
                    {lang.label}
                  </Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
          <div class="pt-3  grid grid-cols-2 gap-2">
            <button
              class="text-sm py-2 rounded-lg bg-surface-700 text-surface-200 hover:bg-surface-600 transition-colors"
              onclick={handleReboot}
            >
              Reboot
            </button>
            <button
              class="text-sm py-2 rounded-lg bg-engage-red/15 text-engage-red hover:bg-engage-red/25 transition-colors"
              onclick={handlePoweroff}
            >
              Power Off
            </button>
          </div>
        </div>
      {/if}
    </CollapsibleCard>

    <!-- Developer Section (collapsed by default, lazy loaded) -->
    <CollapsibleCard title="Developer" bind:open={devTogglesExpanded} onOpenChange={onDevTogglesOpen}>
      {#if !toggles}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else}
        <div class="space-y-4">
          {#each DEV_DEFS as t}
            <div class="flex items-center justify-between gap-3">
              <div class="text-sm text-surface-100">{t.label}</div>
              <Toggle
                checked={toggles[t.key]}
                disabled={toggling === t.key}
                label={t.label}
                onCheckedChange={() => handleToggle(t.key)}
              />
            </div>
            <!-- SSH Keys — right after Enable SSH toggle -->
            {#if t.key === 'SshEnabled' && sshKeys}
              <div class="flex items-center justify-between gap-3">
                <div class="text-sm text-surface-100">SSH Keys</div>
                <div class="flex items-center gap-2 shrink-0">
                  {#if sshKeys.has_keys}
                    <span class="text-sm text-surface-300">{sshKeys.username}</span>
                  {/if}
                  {#if sshError}
                    <span class="text-xs text-engage-red">{sshError}</span>
                  {/if}
                  {#if sshLoading}
                    <button class="px-3 py-1.5 text-xs rounded-lg bg-surface-700 text-surface-400" disabled>
                      <Spinner class="w-3 h-3 inline" />
                    </button>
                  {:else if sshKeys.has_keys}
                    <button
                      class="px-3 py-1.5 text-xs rounded-lg bg-engage-red/15 text-engage-red hover:bg-engage-red/25 transition-colors"
                      onclick={async () => {
                        sshLoading = true; sshError = null
                        try { sshKeys = await removeSshKeys() } catch (e) { sshError = e.message }
                        sshLoading = false
                      }}
                    >REMOVE</button>
                  {:else}
                    <button
                      class="px-3 py-1.5 text-xs rounded-lg bg-engage-blue/15 text-engage-blue hover:bg-engage-blue/25 transition-colors"
                      onclick={async () => {
                        const username = prompt('Enter your GitHub username')
                        if (!username) return
                        sshLoading = true; sshError = null
                        try { sshKeys = await setSshKeys(username) } catch (e) { sshError = e.message }
                        sshLoading = false
                      }}
                    >ADD</button>
                  {/if}
                </div>
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    </CollapsibleCard>
  {/if}
</div>
