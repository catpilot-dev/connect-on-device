<script>
  /** @type {{ events: Array, durationMs: number, height?: string }} */
  let { events = [], durationMs = 0, height = '3px' } = $props()

  // Colors matched to openpilot UI (augmented_road_view.py, alert_renderer.py)
  const EVENT_COLORS = {
    engaged: '#178644',      // openpilot engaged green
    overriding: '#919B95',   // openpilot override grey
    alert_prompt: '#FE8C34', // openpilot userPrompt orange
    alert_critical: '#C92231', // openpilot critical red
    alert: '#FE8C34',        // fallback for untyped alerts
    user_flag: '#EAB308',    // yellow bookmark marker
  }
</script>

<div class="relative w-full rounded-full overflow-hidden" style="height: {height}; background: #173349">
  {#if durationMs > 0}
    {#each events as ev}
      {#if ev.type === 'user_flag'}
        <div
          class="absolute top-0 bottom-0 rounded-full"
          style="left: {(ev.route_offset_millis / durationMs) * 100}%; width: 3px; background: {EVENT_COLORS.user_flag}; z-index: 4"
        ></div>
      {:else if ev.end_route_offset_millis != null}
        {@const color = ev.type === 'alert' ? (ev.alertStatus === 2 ? EVENT_COLORS.alert_critical : EVENT_COLORS.alert_prompt) : (EVENT_COLORS[ev.type] || EVENT_COLORS.engaged)}
        <div
          class="absolute top-0 bottom-0"
          style="left: {(ev.route_offset_millis / durationMs) * 100}%; width: {Math.max(0.3, ((ev.end_route_offset_millis - ev.route_offset_millis) / durationMs) * 100)}%; background: {color}; z-index: {ev.type === 'alert' ? 3 : ev.type === 'overriding' ? 2 : 1}"
        ></div>
      {/if}
    {/each}
  {/if}
</div>
