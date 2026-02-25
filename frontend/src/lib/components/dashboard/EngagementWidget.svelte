<script>
  let { sdState = '', sdEnabled = false, cruiseSpeed = 0, isMetric = true } = $props()

  const stateLabel = $derived({
    'preEnabled': 'Pre-Enabled',
    'enabled': 'Engaged',
    'softDisabling': 'Soft Disabling',
    'overriding': 'Overriding',
    'disabled': 'Disabled',
  }[sdState] || sdState || 'Unknown')

  const stateColor = $derived({
    'enabled': 'bg-engage-green text-black',
    'preEnabled': 'bg-engage-blue text-white',
    'overriding': 'bg-engage-orange text-black',
    'softDisabling': 'bg-engage-red text-white',
    'disabled': 'bg-surface-600 text-surface-300',
  }[sdState] || 'bg-surface-600 text-surface-300')

  const displaySpeed = $derived(cruiseSpeed * (isMetric ? 3.6 : 2.237))
  const speedUnit = $derived(isMetric ? 'km/h' : 'mph')
</script>

<div class="flex flex-col items-center justify-center h-full min-h-[80px] gap-2">
  <!-- State badge -->
  <div class="px-4 py-1.5 rounded-lg text-sm font-semibold {stateColor}">
    {stateLabel}
  </div>

  <!-- Cruise setpoint -->
  {#if cruiseSpeed > 0}
    <div class="flex items-baseline gap-1">
      <span class="text-xl font-bold tabular-nums text-surface-200">{displaySpeed.toFixed(0)}</span>
      <span class="text-xs text-surface-500">{speedUnit} set</span>
    </div>
  {:else}
    <div class="text-sm text-surface-500">No setpoint</div>
  {/if}
</div>
