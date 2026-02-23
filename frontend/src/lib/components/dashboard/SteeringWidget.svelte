<script>
  let { angle = 0, steerCmd = 0 } = $props()

  const clampedAngle = $derived(Math.max(-540, Math.min(540, angle)))
  const cmdPct = $derived(Math.max(-1, Math.min(1, steerCmd)))
</script>

<div class="flex flex-col items-center justify-center h-full min-h-[100px] gap-2">
  <!-- SVG steering wheel -->
  <svg class="w-20 h-20" viewBox="0 0 100 100">
    <g transform="rotate({clampedAngle}, 50, 50)">
      <!-- Outer ring -->
      <circle cx="50" cy="50" r="42" fill="none" stroke="#6b7280" stroke-width="6" />
      <!-- Spokes -->
      <line x1="50" y1="8" x2="50" y2="35" stroke="#6b7280" stroke-width="4" stroke-linecap="round" />
      <line x1="14" y1="68" x2="38" y2="55" stroke="#6b7280" stroke-width="4" stroke-linecap="round" />
      <line x1="86" y1="68" x2="62" y2="55" stroke="#6b7280" stroke-width="4" stroke-linecap="round" />
      <!-- Center hub -->
      <circle cx="50" cy="50" r="8" fill="#374151" stroke="#6b7280" stroke-width="2" />
      <!-- Top marker -->
      <circle cx="50" cy="10" r="3" fill="#3b82f6" />
    </g>
  </svg>

  <div class="text-sm tabular-nums text-surface-300">{angle.toFixed(1)}&deg;</div>

  <!-- Steer command bar -->
  <div class="w-full flex items-center gap-1">
    <span class="text-[10px] text-surface-500 w-4 text-right">L</span>
    <div class="flex-1 h-2 bg-surface-700 rounded-full relative overflow-hidden">
      <!-- Center line -->
      <div class="absolute left-1/2 top-0 w-px h-full bg-surface-500"></div>
      <!-- Command bar -->
      {#if cmdPct >= 0}
        <div class="absolute top-0 h-full bg-engage-green rounded-full transition-all duration-100"
             style="left: 50%; width: {(cmdPct * 50).toFixed(1)}%"></div>
      {:else}
        <div class="absolute top-0 h-full bg-engage-green rounded-full transition-all duration-100"
             style="right: 50%; width: {(-cmdPct * 50).toFixed(1)}%"></div>
      {/if}
    </div>
    <span class="text-[10px] text-surface-500 w-4">R</span>
  </div>
</div>
