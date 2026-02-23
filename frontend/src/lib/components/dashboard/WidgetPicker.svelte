<script>
  let { registry = [], activeIds = [], onpick, onclose } = $props()

  // Group by category
  const grouped = $derived(() => {
    const groups = {}
    for (const w of registry) {
      if (!groups[w.category]) groups[w.category] = []
      groups[w.category].push(w)
    }
    return groups
  })

  let dialogEl = $state(null)

  $effect(() => {
    if (dialogEl && !dialogEl.open) dialogEl.showModal()
  })
</script>

<dialog
  bind:this={dialogEl}
  class="bg-surface-800 text-surface-50 rounded-xl border border-surface-700/50 p-0 w-full max-w-md backdrop:bg-black/60"
  onclose={onclose}
>
  <div class="p-4">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold">Add Widget</h2>
      <button class="text-surface-400 hover:text-surface-200" title="Close" onclick={() => dialogEl.close()}>
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    {#each Object.entries(grouped()) as [category, widgets]}
      <div class="mb-4">
        <h3 class="text-xs font-medium text-surface-400 uppercase tracking-wide mb-2">{category}</h3>
        <div class="grid grid-cols-2 gap-2">
          {#each widgets as w}
            {@const isActive = activeIds.includes(w.id)}
            <button
              class="text-left px-3 py-2 rounded-lg border text-sm transition-colors
                     {isActive ? 'border-surface-600 text-surface-500 cursor-not-allowed bg-surface-800'
                                : 'border-surface-700 text-surface-200 hover:bg-surface-700 hover:border-surface-600'}"
              disabled={isActive}
              onclick={() => { onpick(w.id); dialogEl.close() }}
            >
              {w.label}
              {#if isActive}
                <span class="text-xs text-surface-600 ml-1">added</span>
              {/if}
            </button>
          {/each}
        </div>
      </div>
    {/each}
  </div>
</dialog>
