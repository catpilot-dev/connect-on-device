<script>
  let { label, onremove, children } = $props()
</script>

<div class="card p-0 overflow-hidden flex flex-col aspect-square">
  <div class="drag-handle flex items-center justify-between px-2 py-1 border-b border-surface-700/50 bg-surface-800/50 cursor-grab active:cursor-grabbing">
    <div class="flex items-center gap-1.5 min-w-0">
      <svg class="w-2.5 h-2.5 text-surface-600 shrink-0" viewBox="0 0 16 16" fill="currentColor">
        <circle cx="4" cy="3" r="1.5"/><circle cx="12" cy="3" r="1.5"/>
        <circle cx="4" cy="8" r="1.5"/><circle cx="12" cy="8" r="1.5"/>
        <circle cx="4" cy="13" r="1.5"/><circle cx="12" cy="13" r="1.5"/>
      </svg>
      <span class="text-xs font-medium text-surface-400 uppercase tracking-wide truncate">{label}</span>
    </div>
    {#if onremove}
      <button
        class="text-surface-500 hover:text-surface-200 transition-colors ml-1 shrink-0"
        onclick={onremove}
        title="Remove {label}"
      >
        <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    {/if}
  </div>
  <div class="flex-1" style="container-type: inline-size">
    {@render children()}
  </div>
</div>
