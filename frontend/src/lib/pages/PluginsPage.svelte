<script>
  import { onMount } from 'svelte'
  import Toggle from '../components/Toggle.svelte'
  import Spinner from '../components/Spinner.svelte'
  import ModelPanel from '../components/ModelPanel.svelte'
  import ChevronIcon from '../components/ChevronIcon.svelte'
  import { fetchPlugins, togglePlugin, setPluginParam, deviceReboot } from '../api.js'

  let plugins = $state(null)
  let loading = $state(true)
  let error = $state(null)
  let togglingPlugin = $state(null)
  let needsReboot = $state(false)
  let expandedPlugin = $state(null)
  let paramValues = $state({})  // { pluginId: { key: value } }
  let savingParam = $state(null)

  onMount(async () => {
    try {
      plugins = await fetchPlugins()
      // Initialize local param state from fetched settings
      const vals = {}
      for (const p of plugins) {
        if (p.settings?.length) {
          vals[p.id] = {}
          for (const s of p.settings) {
            vals[p.id][s.key] = s.value
          }
        }
      }
      paramValues = vals
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  })

  async function handleToggle(pluginId) {
    if (togglingPlugin) return
    togglingPlugin = pluginId
    try {
      const result = await togglePlugin(pluginId)
      plugins = plugins.map(p =>
        p.id === pluginId ? { ...p, enabled: result.enabled } : p
      )
      if (result.reboot_required) needsReboot = true
    } catch (e) {
      error = e.message
    } finally {
      togglingPlugin = null
    }
  }

  async function handleParamToggle(pluginId, key) {
    const prev = paramValues[pluginId]?.[key]
    const newVal = !prev
    paramValues[pluginId] = { ...paramValues[pluginId], [key]: newVal }
    savingParam = key
    try {
      await setPluginParam(pluginId, key, newVal)
    } catch (e) {
      paramValues[pluginId] = { ...paramValues[pluginId], [key]: prev }
      error = e.message
    } finally {
      savingParam = null
    }
  }

  async function handleParamPills(pluginId, key, value) {
    const prev = paramValues[pluginId]?.[key]
    paramValues[pluginId] = { ...paramValues[pluginId], [key]: value }
    savingParam = key
    try {
      await setPluginParam(pluginId, key, value)
    } catch (e) {
      paramValues[pluginId] = { ...paramValues[pluginId], [key]: prev }
      error = e.message
    } finally {
      savingParam = null
    }
  }

  async function handleReboot() {
    if (!confirm('Reboot device to apply plugin changes?')) return
    try { await deviceReboot() } catch (e) { error = e.message }
  }

  let enabledCount = $derived(plugins?.filter(p => p.enabled).length ?? 0)
</script>

<div class="w-full max-w-lg mx-auto px-4 py-6 space-y-4 overflow-hidden">
  <div class="flex items-center justify-between">
    <div>
      <h2 class="text-lg font-semibold text-surface-50">Plugins</h2>
      {#if plugins}
        <p class="text-xs text-surface-300 mt-0.5">{enabledCount} of {plugins.length} enabled</p>
      {/if}
    </div>
    {#if needsReboot}
      <button
        class="px-3 py-1.5 text-xs rounded-lg bg-engage-green/15 text-engage-green hover:bg-engage-green/25 transition-colors"
        onclick={handleReboot}
      >Reboot to Apply</button>
    {/if}
  </div>

  {#if error}
    <div class="card p-4 border-engage-red/50">
      <p class="text-engage-red text-sm">{error}</p>
      <button class="btn-ghost text-xs mt-2" onclick={() => { error = null }}>Dismiss</button>
    </div>
  {/if}

  {#if loading}
    <div class="space-y-4">
      {#each [1, 2, 3] as _}
        <div class="card p-4 animate-pulse">
          <div class="h-4 bg-surface-700 rounded w-32 mb-2"></div>
          <div class="h-3 bg-surface-700 rounded w-48"></div>
        </div>
      {/each}
    </div>
  {:else if plugins && plugins.length === 0}
    <div class="card p-6 text-center">
      <p class="text-surface-300 text-sm">No plugins installed</p>
      <p class="text-surface-400 text-xs mt-1">Place plugins in /data/plugins/ on your device</p>
    </div>
  {:else if plugins}
    {#each plugins as plugin}
      <div class="card p-4">
        <div class="flex items-center justify-between gap-3">
          <button
            class="min-w-0 flex-1 text-left"
            onclick={() => expandedPlugin = expandedPlugin === plugin.id ? null : plugin.id}
          >
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-surface-100">{plugin.name}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-600 text-surface-300">{plugin.type}</span>
              {#if plugin.version}
                <span class="text-[10px] text-surface-300">{plugin.version}</span>
              {/if}
              {#if (plugin.panel || plugin.settings?.length) && plugin.enabled}
                <svg class="w-4 h-4 text-surface-100" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M7.84 1.804A1 1 0 0 1 8.82 1h2.36a1 1 0 0 1 .98.804l.331 1.652a6.993 6.993 0 0 1 1.929 1.115l1.598-.54a1 1 0 0 1 1.186.447l1.18 2.044a1 1 0 0 1-.205 1.251l-1.267 1.113a7.047 7.047 0 0 1 0 2.228l1.267 1.113a1 1 0 0 1 .206 1.25l-1.18 2.045a1 1 0 0 1-1.187.447l-1.598-.54a6.993 6.993 0 0 1-1.929 1.115l-.33 1.652a1 1 0 0 1-.98.804H8.82a1 1 0 0 1-.98-.804l-.331-1.652a6.993 6.993 0 0 1-1.929-1.115l-1.598.54a1 1 0 0 1-1.186-.447l-1.18-2.044a1 1 0 0 1 .205-1.251l1.267-1.114a7.05 7.05 0 0 1 0-2.227L1.821 7.773a1 1 0 0 1-.206-1.25l1.18-2.045a1 1 0 0 1 1.187-.447l1.598.54A6.992 6.992 0 0 1 7.51 3.456l.33-1.652ZM10 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" clip-rule="evenodd" />
                </svg>
              {/if}
            </div>
            {#if plugin.description}
              <p class="text-xs text-surface-300 mt-1">{plugin.description}</p>
            {/if}
            {#if plugin.dependencies?.length > 0}
              <div class="flex items-center gap-1 mt-1.5">
                <span class="text-[10px] text-surface-400">deps:</span>
                {#each plugin.dependencies as dep}
                  <span class="text-[10px] px-1 py-0.5 rounded bg-surface-700 text-surface-300">{dep}</span>
                {/each}
              </div>
            {/if}
          </button>
          <Toggle
            checked={plugin.enabled}
            disabled={togglingPlugin === plugin.id}
            label={plugin.name}
            onCheckedChange={() => handleToggle(plugin.id)}
          />
        </div>

        {#if expandedPlugin === plugin.id && plugin.enabled}
          {#if plugin.id === 'model_selector' && plugin.panel}
            <div class="pt-4 border-t border-surface-700 mt-4">
              <ModelPanel />
            </div>
          {/if}
          {#if plugin.settings?.length}
            <div class="pt-4 border-t border-surface-700 mt-4 space-y-4">
              {#each plugin.settings as setting}
                {#if setting.type === 'bool'}
                  <div class="flex items-center justify-between gap-4">
                    <div>
                      <div class="text-sm text-surface-100">{setting.label}</div>
                      <div class="text-xs text-surface-300 mt-0.5">{setting.desc}</div>
                    </div>
                    <Toggle
                      checked={paramValues[plugin.id]?.[setting.key] ?? false}
                      disabled={savingParam === setting.key}
                      label={setting.label}
                      onCheckedChange={() => handleParamToggle(plugin.id, setting.key)}
                    />
                  </div>
                {:else if setting.type === 'pills'}
                  {@const depDisabled = setting.dependsOn ? !paramValues[plugin.id]?.[setting.dependsOn] : false}
                  <div class="{depDisabled ? 'opacity-40 pointer-events-none' : ''}">
                    <div class="flex items-center gap-2">
                      <div class="text-sm text-surface-100">{setting.label}</div>
                      <div class="flex-1"></div>
                      {#each setting.options as opt, i}
                        {@const val = typeof opt === 'string' ? i : opt}
                        {@const active = (paramValues[plugin.id]?.[setting.key] ?? -1) === val}
                        <button
                          class="px-2.5 py-1 text-xs rounded-full transition-colors {active ? 'bg-engage-blue/20 text-engage-blue border border-engage-blue/40' : 'bg-surface-700 text-surface-300 border border-surface-600 hover:border-surface-500'}"
                          disabled={depDisabled}
                          onclick={() => handleParamPills(plugin.id, setting.key, val)}
                        >
                          {opt}{setting.suffix || ''}
                        </button>
                      {/each}
                    </div>
                    {#if setting.desc}<div class="text-xs text-surface-300 mt-0.5">{setting.desc}</div>{/if}
                  </div>
                {/if}
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    {/each}
  {/if}
</div>
