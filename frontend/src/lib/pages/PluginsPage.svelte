<script>
  import { onMount } from 'svelte'
  import Toggle from '../components/Toggle.svelte'
  import Spinner from '../components/Spinner.svelte'
  import { fetchPlugins, togglePlugin, deviceReboot } from '../api.js'

  let plugins = $state(null)
  let loading = $state(true)
  let error = $state(null)
  let togglingPlugin = $state(null)
  let needsReboot = $state(false)

  onMount(async () => {
    try {
      plugins = await fetchPlugins()
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
        <p class="text-xs text-surface-500 mt-0.5">{enabledCount} of {plugins.length} enabled</p>
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
      <p class="text-surface-400 text-sm">No plugins installed</p>
      <p class="text-surface-600 text-xs mt-1">Place plugins in /data/plugins/ on your device</p>
    </div>
  {:else if plugins}
    {#each plugins as plugin}
      <div class="card p-4">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-surface-100">{plugin.name}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-600 text-surface-400">{plugin.type}</span>
              {#if plugin.version}
                <span class="text-[10px] text-surface-500">{plugin.version}</span>
              {/if}
            </div>
            {#if plugin.description}
              <p class="text-xs text-surface-500 mt-1">{plugin.description}</p>
            {/if}
            {#if plugin.dependencies?.length > 0}
              <div class="flex items-center gap-1 mt-1.5">
                <span class="text-[10px] text-surface-600">deps:</span>
                {#each plugin.dependencies as dep}
                  <span class="text-[10px] px-1 py-0.5 rounded bg-surface-700 text-surface-500">{dep}</span>
                {/each}
              </div>
            {/if}
          </div>
          <Toggle
            checked={plugin.enabled}
            disabled={togglingPlugin === plugin.id}
            label={plugin.name}
            onCheckedChange={() => handleToggle(plugin.id)}
          />
        </div>
      </div>
    {/each}
  {/if}
</div>
