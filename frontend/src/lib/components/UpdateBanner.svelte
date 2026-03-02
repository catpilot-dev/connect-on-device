<script>
  let { updates, isOnroad = false, onDismiss, onUpdate } = $props()

  let applying = $state(false)
  let result = $state(null)

  let names = $derived.by(() => {
    const parts = []
    if (updates?.cod?.available) parts.push('COD')
    if (updates?.plugins?.available) parts.push('Plugins')
    return parts.join(' \u00b7 ')
  })

  async function handleUpdate() {
    applying = true
    result = null
    try {
      const data = await onUpdate()
      result = data
    } catch {
      result = { status: 'error' }
    }
    applying = false
  }
</script>

<div class="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2">
  <div class="mx-auto max-w-6xl flex items-center justify-between gap-4 text-sm">
    <div class="text-amber-400 min-w-0">
      {#if result?.reboot_required}
        <span>Plugins updated — reboot required for changes to take effect.</span>
      {:else if result?.cod_updated}
        <span>COD updated — refreshing...</span>
      {:else if result?.status === 'error'}
        <span>Update failed. Try again later.</span>
      {:else if applying}
        <span class="inline-flex items-center gap-2">
          <svg class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" opacity="0.25"/>
            <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          Updating...
        </span>
      {:else if isOnroad}
        <span>Updates available: {names} (stop driving to update)</span>
      {:else}
        <span>Updates available: {names}</span>
      {/if}
    </div>

    <div class="flex items-center gap-2 shrink-0">
      {#if !result && !applying && !isOnroad}
        <button
          class="px-3 py-1 rounded bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 transition-colors text-xs font-medium"
          onclick={handleUpdate}
        >
          Update
        </button>
      {/if}
      {#if result?.reboot_required && !isOnroad}
        <button
          class="px-3 py-1 rounded bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 transition-colors text-xs font-medium"
          onclick={() => fetch('/v1/device/reboot', { method: 'POST' })}
        >
          Reboot
        </button>
      {/if}
      <button
        class="text-amber-500/60 hover:text-amber-400 transition-colors p-0.5"
        onclick={onDismiss}
        aria-label="Dismiss"
      >
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 6L6 18M6 6l12 12"/>
        </svg>
      </button>
    </div>
  </div>
</div>
