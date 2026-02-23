<script>
  let { histories = [], colors = ['#ef4444', '#f97316'], labels = [], scale = 1, cursorTime = null } = $props()

  const viewW = 300
  const viewH = 60

  // Compute global min/max across all series
  const bounds = $derived(() => {
    let min = Infinity, max = -Infinity
    for (const h of histories) {
      for (const pt of h) {
        const v = pt.v * scale
        if (v < min) min = v
        if (v > max) max = v
      }
    }
    if (max === min) { min -= 1; max += 1 }
    const pad = (max - min) * 0.1
    return { min: min - pad, max: max + pad }
  })

  const allPoints = $derived(() => {
    const { min, max } = bounds()
    return histories.map(h => {
      if (!h.length) return ''
      return h.map((pt, i) => {
        const x = (i / Math.max(h.length - 1, 1)) * viewW
        const y = viewH - ((pt.v * scale - min) / (max - min)) * viewH
        return `${x.toFixed(1)},${y.toFixed(1)}`
      }).join(' ')
    })
  })

  const currentValues = $derived(
    histories.map(h => h.length > 0 ? (h[h.length - 1].v * scale) : 0)
  )

  const cursorX = $derived(() => {
    const h = histories[0]
    if (cursorTime == null || !h || h.length < 2) return null
    const t0 = h[0].t, t1 = h[h.length - 1].t
    if (t1 <= t0) return null
    const pct = (cursorTime - t0) / (t1 - t0)
    if (pct < 0 || pct > 1) return null
    return pct * viewW
  })
</script>

<div class="flex flex-col h-full min-h-[60px]">
  <!-- Legend -->
  <div class="flex items-center gap-3 mb-1">
    {#each currentValues as val, i}
      <div class="flex items-center gap-1">
        <div class="w-2 h-2 rounded-full" style="background-color: {colors[i] || '#888'}"></div>
        <span class="text-xs tabular-nums text-surface-300">
          {labels[i] || `S${i + 1}`}: {val.toFixed(0)}
        </span>
      </div>
    {/each}
  </div>

  <svg class="w-full flex-1" viewBox="0 0 {viewW} {viewH}" preserveAspectRatio="none">
    {#each allPoints() as pts, i}
      {#if pts}
        <polyline
          points={pts}
          fill="none"
          stroke={colors[i] || '#888'}
          stroke-width="1.5"
          vector-effect="non-scaling-stroke"
        />
      {/if}
    {/each}
    {#if cursorX() != null}
      <line x1={cursorX()} y1="0" x2={cursorX()} y2={viewH}
            stroke="white" stroke-width="1" opacity="0.5" vector-effect="non-scaling-stroke" />
    {/if}
  </svg>
</div>
