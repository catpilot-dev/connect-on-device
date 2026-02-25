<script>
  let { label, onremove, children } = $props()
</script>

<div class="card p-0 overflow-hidden flex flex-col">
  <div class="drag-handle flex items-center justify-between px-3 py-1.5 border-b border-surface-700/50 bg-surface-800/50 cursor-grab active:cursor-grabbing">
    <div class="flex items-center gap-1.5 min-w-0">
      <svg class="w-3 h-3 text-surface-600 shrink-0" viewBox="0 0 16 16" fill="currentColor">
        <circle cx="4" cy="3" r="1.5"/><circle cx="12" cy="3" r="1.5"/>
        <circle cx="4" cy="8" r="1.5"/><circle cx="12" cy="8" r="1.5"/>
        <circle cx="4" cy="13" r="1.5"/><circle cx="12" cy="13" r="1.5"/>
      </svg>
      <span class="text-xs font-medium text-surface-400 uppercase tracking-wide truncate">{label}</span>
    </div>
    {#if onremove}
      <button
        class="text-surface-500 hover:text-surface-200 transition-colors ml-2 shrink-0"
        onclick={onremove}
        title="Remove widget"
      >
        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    {/if}
  </div>
  <div class="flex-1 p-3">
    {@render children()}
  </div>
</div>
