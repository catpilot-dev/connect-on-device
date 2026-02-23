<script>
  import { onMount, onDestroy } from 'svelte'
  import { Select, ToggleGroup } from 'bits-ui'
  import CollapsibleCard from '../components/CollapsibleCard.svelte'
  import Spinner from '../components/Spinner.svelte'
  import ChevronIcon from '../components/ChevronIcon.svelte'
  import Toggle from '../components/Toggle.svelte'
  import { createPoll } from '../utils/poll.js'
  import { fetchParams, setParam, fetchModels, swapModel, checkModelUpdates, downloadModel,
    fetchSoftware, softwareCheck, softwareDownload, softwareInstall, softwareBranch, softwareUninstall,
    fetchLateralDelay, fetchDeviceInfo, deviceReboot, devicePoweroff, deviceSetLanguage,
    fetchToggles, setToggle, fetchStorage, mapdCheckUpdate, mapdUpdate,
    fetchSshKeys, setSshKeys, removeSshKeys } from '../api.js'
  import { getTileSource, setTileSource, TILE_SOURCES } from '../tileSource.js'
  import { formatBytes, storageLevel } from '../format.js'

  let params = $state({})
  let loading = $state(true)
  let error = $state(null)
  let saving = $state(null)
  let latDelay = $state(null)
  let tileSource = $state(getTileSource())

  // Panel expanded state
  let devExpanded = $state(false)
  let togglesExpanded = $state(false)
  let devTogglesExpanded = $state(false)
  let sectionExpanded = $state({ 'Driving': true, 'Speed Limits': true })
  let mapdExpanded = $state(false)
  let swExpanded = $state(false)
  let modelsExpanded = $state(false)

  // Model state
  let models = $state(null)
  let modelsLoading = $state(true)
  let modelsError = $state(null)
  let swapping = $state(null)
  let swapResult = $state(null)
  let updates = $state(null)
  let checking = $state(false)
  let downloading = $state(null)

  // Software update state
  let sw = $state(null)
  let swLoading = $state(true)
  let swError = $state(null)
  let swChecking = $state(false)
  let swDownloading = $state(false)
  let swChecked = $state(false)

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

  // Poll timers (cleaned up on destroy)
  const swPoll = createPoll(async () => {
    try {
      sw = await fetchSoftware()
      if (!sw.UpdaterState || sw.UpdaterState === 'idle') {
        swPoll.stop()
        if (swChecking) { swChecking = false; swChecked = true }
        if (swDownloading) swDownloading = false
      }
    } catch { /* ignore */ }
  }, 2000)

  const downloadPoll = createPoll(async () => {
    try {
      const fresh = await fetchModels()
      models = fresh
      if (!fresh.download || fresh.download.status !== 'downloading') {
        downloadPoll.stop()
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
    } catch { /* ignore */ }
  }, 3000)

  onDestroy(() => { swPoll.stop(); downloadPoll.stop() })

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
        { key: 'MapdSpeedLimitOffsetPercent', label: 'Speed Limit Offset', desc: 'Percentage above the posted speed limit', type: 'int', options: [0, 5, 10, 15], dependsOn: 'MapdSpeedLimitControlEnabled' },
        { key: 'MapdCurveTargetLatAccel', label: 'Curve Comfort', desc: 'Target lateral acceleration in curves (m/s²). Lower = gentler, higher = sportier.', type: 'choice', options: ['1.5', '2.0', '2.5', '3.0'], dependsOn: 'MapdSpeedLimitControlEnabled' },
      ],
    },
  ]

  // Derived: find the active model object from the list
  let activeDriving = $derived(models?.driving?.find(m => m.id === models?.active_driving))
  let activeDm = $derived(models?.dm?.find(m => m.id === models?.active_dm))
  let storagePct = $derived(storage ? Math.round(100 - storage.percent_free) : 0)
  let storageColor = $derived(storage ? storageLevel(storage.percent_free) : 'ok')

  onMount(async () => {
    try {
      params = await fetchParams()
      if (params.MapdVersion) mapdVersion = params.MapdVersion
      fetchLateralDelay().then(d => { latDelay = d }).catch(() => {})
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
    loadModels()
    loadSoftware()
    fetchDeviceInfo().then(d => { dev = d }).catch(() => {})
    fetchToggles().then(t => { toggles = t }).catch(() => {})
    fetchStorage().then(s => { storage = s }).catch(() => {})
    fetchSshKeys().then(s => { sshKeys = s }).catch(() => {})
  })

  async function loadModels() {
    modelsLoading = true
    modelsError = null
    try {
      models = await fetchModels()
      if (models.download?.status === 'downloading') {
        downloading = models.download.model_id
        downloadPoll.start()
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
      // Reflect existing state on load
      if (sw.UpdateAvailable) swChecked = true
      if (sw.UpdaterFetchAvailable) swChecked = true
      if (sw.UpdaterState && sw.UpdaterState !== 'idle') {
        if (sw.UpdaterState === 'checking') swChecking = true
        else swDownloading = true
        swPoll.start()
      }
    } catch (e) {
      swError = e.message
    } finally {
      swLoading = false
    }
  }

  async function handleSwAutoCheck() {
    if (swChecking || swDownloading) return
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

  async function handleSwUpdate() {
    swDownloading = true
    swError = null
    try {
      await softwareDownload()
      swPoll.start()
    } catch (e) {
      swError = e.message
      swDownloading = false
    }
  }

  async function handleSwReboot() {
    if (!confirm('Reboot to apply update?')) return
    try {
      await softwareInstall()
    } catch (e) {
      swError = e.message
    }
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
    if (value === toggles?.LongitudinalPersonality) return
    const prev = toggles.LongitudinalPersonality
    toggles.LongitudinalPersonality = value
    try {
      await setToggle('LongitudinalPersonality', value)
    } catch (e) {
      toggles.LongitudinalPersonality = prev
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

  async function handleDownload(type, modelId) {
    downloading = modelId
    try {
      await downloadModel(type, modelId)
      downloadPoll.start()
    } catch (e) {
      modelsError = e.message
      downloading = null
    }
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

<div class="max-w-lg mx-auto px-4 py-6 space-y-6 overflow-hidden">
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

    <!-- Device Section (collapsed by default) -->
    {#if dev}
      <CollapsibleCard title="Device" metadata={dev.DongleId || '--'} bind:open={devExpanded}>
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
                    class="h-full rounded-full transition-all duration-500"
                    class:bg-engage-green={storageColor === 'ok'}
                    class:bg-engage-orange={storageColor === 'warning'}
                    class:bg-engage-red={storageColor === 'critical'}
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
          <div class="pt-3 border-t border-surface-700 grid grid-cols-2 gap-2">
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
      </CollapsibleCard>
    {/if}

    <!-- Toggles Section (collapsed by default) -->
    {#if toggles}
      <CollapsibleCard title="Toggles" bind:open={togglesExpanded}>
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
          <div class="pt-3 border-t border-surface-700">
            <div class="text-sm text-surface-100 mb-2">Driving Personality</div>
            <ToggleGroup.Root
              type="single"
              value={String(toggles.LongitudinalPersonality)}
              onValueChange={(v) => { if (v) handlePersonality(Number(v)) }}
              class="grid grid-cols-3 gap-1 rounded-lg bg-surface-700 p-1"
            >
              {#each PERSONALITIES as p}
                <ToggleGroup.Item
                  value={String(p.value)}
                  class="text-xs py-1.5 rounded-md transition-colors
                    data-[state=on]:bg-engage-blue data-[state=on]:text-white
                    data-[state=off]:text-surface-300 data-[state=off]:hover:text-surface-100"
                >
                  {p.label}
                </ToggleGroup.Item>
              {/each}
            </ToggleGroup.Root>
          </div>
        </div>
      </CollapsibleCard>
    {/if}

    <!-- Developer Section (collapsed by default) -->
    {#if toggles}
      <CollapsibleCard title="Developer" bind:open={devTogglesExpanded}>
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
      </CollapsibleCard>
    {/if}

    {#each SECTIONS as section}
      <CollapsibleCard title={section.title} bind:open={sectionExpanded[section.title]}>
        <div class="space-y-4">
          {#each section.items as item}
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
            {:else if item.type === 'int'}
              {@const disabled = item.dependsOn ? !params[item.dependsOn] : false}
              <div class={disabled ? 'opacity-40' : ''}>
                <div class="text-sm text-surface-100">{item.label}</div>
                <div class="text-xs text-surface-500 mt-0.5 mb-2">{item.desc}</div>
                <ToggleGroup.Root
                  type="single"
                  value={String(params[item.key])}
                  onValueChange={(v) => { if (v) setOffset(item.key, Number(v)) }}
                  {disabled}
                  class="flex gap-2"
                >
                  {#each item.options as opt}
                    <ToggleGroup.Item
                      value={String(opt)}
                      disabled={saving === item.key}
                      class="px-3 py-1.5 text-sm rounded-lg transition-colors
                        data-[state=on]:bg-engage-blue data-[state=on]:text-white
                        data-[state=off]:bg-surface-700 data-[state=off]:text-surface-300 data-[state=off]:hover:bg-surface-600"
                    >
                      {opt}%
                    </ToggleGroup.Item>
                  {/each}
                </ToggleGroup.Root>
              </div>
            {:else if item.type === 'choice'}
              {@const disabled = item.dependsOn ? !params[item.dependsOn] : false}
              <div class={disabled ? 'opacity-40' : ''}>
                <div class="text-sm text-surface-100">{item.label}</div>
                <div class="text-xs text-surface-500 mt-0.5 mb-2">{item.desc}</div>
                <ToggleGroup.Root
                  type="single"
                  value={String(params[item.key])}
                  onValueChange={(v) => { if (v) setOffset(item.key, Number(v)) }}
                  {disabled}
                  class="flex gap-2"
                >
                  {#each item.options as opt, i}
                    <ToggleGroup.Item
                      value={String(i)}
                      disabled={saving === item.key}
                      class="px-3 py-1.5 text-sm rounded-lg transition-colors
                        data-[state=on]:bg-engage-blue data-[state=on]:text-white
                        data-[state=off]:bg-surface-700 data-[state=off]:text-surface-300 data-[state=off]:hover:bg-surface-600"
                    >
                      {opt}
                    </ToggleGroup.Item>
                  {/each}
                </ToggleGroup.Root>
              </div>
            {/if}
          {/each}
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
      </CollapsibleCard>
    {/each}

    <!-- Mapd & Maps -->
    <CollapsibleCard
      title="Mapd & Maps"
      metadata={mapdVersion ? `${mapdVersion}${mapdReleaseDate ? ` / ${mapdReleaseDate}` : ''}` : ''}
      bind:open={mapdExpanded}
      onOpenChange={(open) => { if (open && !mapdLatest && !mapdChecking) handleMapdCheck() }}
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
        <div class="pt-3 border-t border-surface-700">
          <a href="/tiles" class="flex items-center justify-between group">
            <div>
              <div class="text-sm text-surface-100">Map Tiles Management</div>
              <div class="text-xs text-surface-500 mt-0.5">Download and manage OSM offline tiles</div>
            </div>
            <svg class="w-5 h-5 text-surface-500 group-hover:text-surface-300 transition-colors" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clip-rule="evenodd" />
            </svg>
          </a>
        </div>
        <div class="pt-3 border-t border-surface-700">
          <div class="text-sm text-surface-100 mb-2">Map Tile Source</div>
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
      </div>
    </CollapsibleCard>

    <!-- Software Section (collapsed by default) -->
    <CollapsibleCard
      title="Software"
      metadata={sw ? `${sw.GitBranch} / ${sw.GitCommit?.slice(0, 7) || '???'}` : ''}
      bind:open={swExpanded}
      onOpenChange={(open) => { if (open && !swChecked && !swChecking && !swDownloading && !sw?.UpdateAvailable) handleSwAutoCheck() }}
    >
      {#if swLoading}
        <div class="space-y-3 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-48"></div>
          <div class="h-4 bg-surface-700 rounded w-32"></div>
        </div>
      {:else if sw}
        <div class="space-y-4">
          <div class="text-xs text-surface-500 space-y-1">
            {#if sw.UpdaterCurrentDescription}
              <div class="text-sm text-surface-100">{sw.UpdaterCurrentDescription}</div>
            {/if}
            <div>Branch: <span class="text-surface-300">{sw.GitBranch}</span></div>
            <div>Commit: <span class="text-surface-300">{sw.GitCommit?.slice(0, 7) || '???'}</span></div>
            {#if sw.GitCommitDate}
              <div>Date: <span class="text-surface-300">{sw.GitCommitDate}</span></div>
            {/if}
          </div>
          {#if (sw.UpdaterFetchAvailable || sw.UpdateAvailable) && sw.UpdaterNewDescription}
            <div class="text-sm text-engage-green">{sw.UpdaterNewDescription}</div>
          {/if}
          {#if swError}
            <div class="text-xs text-engage-red">{swError}</div>
          {/if}
          {#if !sw.IsTestedBranch && sw.UpdaterAvailableBranches?.length > 0}
            <div class="pt-3 border-t border-surface-700">
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
          <div class="pt-3 border-t border-surface-700 grid grid-cols-2 gap-2">
            {#if swDownloading}
              <button class="text-sm py-2 rounded-lg bg-surface-700 text-surface-400 flex items-center justify-center gap-2" disabled>
                <Spinner />
                Updating
              </button>
            {:else if swChecking}
              <button class="text-sm py-2 rounded-lg bg-surface-700 text-surface-400 flex items-center justify-center gap-2" disabled>
                <Spinner />
                Checking
              </button>
            {:else if sw.UpdateAvailable}
              <button
                class="text-sm py-2 rounded-lg bg-engage-green/15 text-engage-green hover:bg-engage-green/25 transition-colors"
                onclick={handleSwReboot}
              >
                Reboot
              </button>
            {:else if sw.UpdaterFetchAvailable}
              <button
                class="text-sm py-2 rounded-lg bg-engage-green/15 text-engage-green hover:bg-engage-green/25 transition-colors"
                onclick={handleSwUpdate}
              >
                Update
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

    <!-- Models Section -->
    <CollapsibleCard title="Models" metadata={activeDriving?.name ?? ''} bind:open={modelsExpanded}>
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
                  <Spinner class="w-4 h-4 text-surface-400 shrink-0" />
                {:else}
                  <ChevronIcon class="text-surface-400 shrink-0" />
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
                  <ChevronIcon class="text-surface-400 shrink-0" />
                </Select.Trigger>
                <Select.Content
                  class="z-50 rounded-lg bg-surface-700 border border-surface-600 shadow-xl py-1 max-h-64 overflow-y-auto max-w-[calc(100vw-2rem)]"
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
              <Spinner />
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
                          <Spinner class="w-3 h-3" />
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
                          <Spinner class="w-3 h-3" />
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
    </CollapsibleCard>
  {/if}
</div>
