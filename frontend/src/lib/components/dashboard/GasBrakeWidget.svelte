<script>
  let { gasPressed = false, brakePressed = false, accelCmd = 0 } = $props()

  const accelPct = $derived(Math.max(-1, Math.min(1, accelCmd)))
</script>

<div class="flex flex-col justify-center h-full min-h-[80px] gap-3">
  <!-- Gas bar -->
  <div class="flex items-center gap-2">
    <span class="text-xs w-10 text-right {gasPressed ? 'text-engage-green font-medium' : 'text-surface-500'}">Gas</span>
    <div class="flex-1 h-3 bg-surface-700 rounded-full overflow-hidden">
      <div class="h-full rounded-full transition-all duration-100"
           style="width: {gasPressed ? '100%' : '0%'}; background-color: #22c55e"></div>
    </div>
  </div>

  <!-- Brake bar -->
  <div class="flex items-center gap-2">
    <span class="text-xs w-10 text-right {brakePressed ? 'text-engage-red font-medium' : 'text-surface-500'}">Brake</span>
    <div class="flex-1 h-3 bg-surface-700 rounded-full overflow-hidden">
      <div class="h-full rounded-full transition-all duration-100"
           style="width: {brakePressed ? '100%' : '0%'}; background-color: #ef4444"></div>
    </div>
  </div>

  <!-- Accel command bar (centered) -->
  <div class="flex items-center gap-2">
    <span class="text-xs w-10 text-right text-surface-500">Cmd</span>
    <div class="flex-1 h-2.5 bg-surface-700 rounded-full relative overflow-hidden">
      <div class="absolute left-1/2 top-0 w-px h-full bg-surface-500"></div>
      {#if accelPct >= 0}
        <div class="absolute top-0 h-full bg-engage-green rounded-full transition-all duration-100"
             style="left: 50%; width: {(accelPct * 50).toFixed(1)}%"></div>
      {:else}
        <div class="absolute top-0 h-full bg-engage-red rounded-full transition-all duration-100"
             style="right: 50%; width: {(-accelPct * 50).toFixed(1)}%"></div>
      {/if}
    </div>
    <span class="text-xs tabular-nums text-surface-400 w-12">{accelCmd.toFixed(2)}</span>
  </div>
</div>
