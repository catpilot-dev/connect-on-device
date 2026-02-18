<script>
  /** @type {{ events: Array, durationMs: number, height?: string }} */
  let { events = [], durationMs = 0, height = '3px' } = $props()

  const EVENT_COLORS = {
    engaged: '#22c55e',
    overriding: '#3b82f6',
    alert: '#f59e0b',
    user_flag: '#ef4444',
  }
</script>

<div class="relative w-full rounded-full overflow-hidden bg-surface-700/50" style="height: {height}">
  {#if durationMs > 0}
    {#each events as ev}
      {#if ev.type === 'user_flag'}
        <div
          class="absolute top-0 bottom-0 rounded-full"
          style="left: {(ev.route_offset_millis / durationMs) * 100}%; width: 3px; background: {EVENT_COLORS.user_flag}; z-index: 4"
        ></div>
      {:else if ev.end_route_offset_millis != null}
        <div
          class="absolute top-0 bottom-0"
          style="left: {(ev.route_offset_millis / durationMs) * 100}%; width: {Math.max(0.3, ((ev.end_route_offset_millis - ev.route_offset_millis) / durationMs) * 100)}%; background: {EVENT_COLORS[ev.type] || EVENT_COLORS.engaged}; z-index: {ev.type === 'alert' ? 3 : ev.type === 'overriding' ? 2 : 1}"
        ></div>
      {/if}
    {/each}
  {/if}
</div>
