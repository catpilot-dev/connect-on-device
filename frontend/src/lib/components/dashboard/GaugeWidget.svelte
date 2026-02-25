<script>
  let { value = 0, unit = '', range = [0, 100], zones = [], scale = 1, offset = 0, history = [], color = '#3b82f6' } = $props()

  const displayValue = $derived(value * scale + offset)
  const pct = $derived(range[1] > range[0] ? Math.max(0, Math.min(1, (displayValue - range[0]) / (range[1] - range[0]))) : 0)

  const zoneColor = $derived(() => {
    if (!zones.length) return 'text-surface-50'
    for (const z of zones) {
      if (displayValue <= z.to) return `text-${z.color === 'blue' ? 'blue-400' : z.color === 'green' ? 'engage-green' : z.color === 'red' ? 'engage-red' : z.color === 'yellow' ? 'yellow-400' : 'surface-50'}`
    }
    return 'text-surface-50'
  })

  // Mini sparkline from history (last 60 points)
  const sparkPoints = $derived(() => {
    if (!history.length) return ''
    const data = history.slice(-60)
    const min = range[0], max = range[1]
    const h = 20, w = 100
    return data.map((v, i) => {
      const x = (i / Math.max(data.length - 1, 1)) * w
      const y = h - ((v * scale + offset - min) / (max - min || 1)) * h
      return `${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  })
</script>

<div class="flex flex-col items-center justify-center h-full min-h-[80px]">
  <div class="text-3xl font-bold tabular-nums {zoneColor()}">
    {displayValue.toFixed(unit === 'V' ? 1 : 0)}
  </div>
  <div class="text-xs text-surface-500 mt-0.5">{unit}</div>

  <!-- Progress bar -->
  <div class="w-full h-1.5 bg-surface-700 rounded-full mt-2 overflow-hidden">
    <div class="h-full rounded-full transition-all duration-200"
         style="width: {(pct * 100).toFixed(1)}%; background-color: {color}"></div>
  </div>

  <!-- Mini sparkline footer -->
  {#if sparkPoints()}
    <svg class="w-full h-5 mt-1" viewBox="0 0 100 20" preserveAspectRatio="none">
      <polyline
        points={sparkPoints()}
        fill="none"
        stroke={color}
        stroke-width="1"
        vector-effect="non-scaling-stroke"
      />
    </svg>
  {/if}
</div>
