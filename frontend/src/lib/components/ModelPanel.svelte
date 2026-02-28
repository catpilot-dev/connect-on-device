<script>
  import { onMount, onDestroy } from 'svelte'
  import { Select } from 'bits-ui'
  import Spinner from './Spinner.svelte'
  import ChevronIcon from './ChevronIcon.svelte'
  import { createPoll } from '../utils/poll.js'
  import { fetchModels, fetchModelsActive, swapModel, checkModelUpdates, downloadModel, deviceReboot } from '../api.js'

  let models = $state(null)
  let modelsLoading = $state(true)
  let modelsError = $state(null)
  let swapping = $state(null)
  let swapResult = $state(null)
  let swapRebooting = $state(false)
  let updates = $state(null)
  let checking = $state(false)
  let downloading = $state(null)

  let activeDriving = $derived(models?.driving?.find(m => m.id === models?.active_driving))
  let activeDm = $derived(models?.dm?.find(m => m.id === models?.active_dm))

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

  onDestroy(() => { downloadPoll.stop() })

  onMount(() => { loadModels() })

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
</script>

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
    <div class="rounded-lg px-3 py-2 text-sm mb-3 flex items-center justify-between gap-2 {swapResult.includes('failed') ? 'bg-engage-red/10 text-engage-red' : 'bg-engage-green/10 text-engage-green'}">
      <span>{swapResult}</span>
      <div class="flex items-center gap-1 shrink-0">
        {#if !swapResult.includes('failed')}
          <button
            class="px-2.5 py-1 text-xs rounded-lg transition-colors {swapRebooting ? 'bg-surface-700 text-surface-400' : 'bg-engage-green/20 hover:bg-engage-green/30'}"
            disabled={swapRebooting}
            onclick={() => { swapRebooting = true; deviceReboot().catch(() => {}) }}
          >{swapRebooting ? 'Rebooting...' : 'Reboot'}</button>
        {/if}
        <button class="px-1 opacity-60 hover:opacity-100" onclick={() => { swapResult = null }}>x</button>
      </div>
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
          <span class="text-xs text-surface-400 font-medium shrink-0">Driving</span>
          <div class="flex items-center gap-3">
            <span class="text-sm text-surface-100">{activeDriving?.name ?? '...'}</span>
            {#if activeDriving?.date}
              <span class="text-xs text-surface-500">{activeDriving.date}</span>
            {/if}
            {#if swapping}
              <Spinner class="w-4 h-4 text-surface-400 shrink-0" />
            {:else}
              <ChevronIcon class="text-surface-400 shrink-0" />
            {/if}
          </div>
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
            <span class="text-xs text-surface-400 font-medium uppercase shrink-0">DM</span>
            <div class="flex items-center gap-3">
              <span class="text-sm text-surface-100">{activeDm?.name ?? '...'}</span>
              {#if activeDm?.date}
                <span class="text-xs text-surface-500">{activeDm.date}</span>
              {/if}
              <ChevronIcon class="text-surface-400 shrink-0" />
            </div>
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
  <div class="pt-3 mt-3">
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
