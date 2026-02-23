<script>
  let { history = [], scale = 1, color = '#3b82f6', cursorTime = null } = $props()

  // Compute SVG points from history ring buffer
  const viewW = 300
  const viewH = 60

  const points = $derived(() => {
    if (!history.length) return ''
    let min = Infinity, max = -Infinity
    for (const pt of history) {
      const v = pt.v * scale
      if (v < min) min = v
      if (v > max) max = v
    }
    if (max === min) { min -= 1; max += 1 }
    const pad = (max - min) * 0.1
    min -= pad; max += pad

    return history.map((pt, i) => {
      const x = (i / Math.max(history.length - 1, 1)) * viewW
      const y = viewH - ((pt.v * scale - min) / (max - min)) * viewH
      return `${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  })

  // Current value display
  const currentValue = $derived(
    history.length > 0 ? (history[history.length - 1].v * scale) : 0
  )

  // Cursor position for replay mode
  const cursorX = $derived(() => {
    if (cursorTime == null || history.length < 2) return null
    const t0 = history[0].t, t1 = history[history.length - 1].t
    if (t1 <= t0) return null
    const pct = (cursorTime - t0) / (t1 - t0)
    if (pct < 0 || pct > 1) return null
    return pct * viewW
  })
</script>

<div class="flex flex-col h-full min-h-[60px]">
  <div class="text-right text-sm tabular-nums text-surface-300 mb-1">
    {currentValue.toFixed(1)}
  </div>
  <svg class="w-full flex-1" viewBox="0 0 {viewW} {viewH}" preserveAspectRatio="none">
    {#if points()}
      <polyline
        points={points()}
        fill="none"
        stroke={color}
        stroke-width="1.5"
        vector-effect="non-scaling-stroke"
      />
    {/if}
    <!-- Replay cursor -->
    {#if cursorX() != null}
      <line x1={cursorX()} y1="0" x2={cursorX()} y2={viewH}
            stroke="white" stroke-width="1" opacity="0.5" vector-effect="non-scaling-stroke" />
    {/if}
  </svg>
</div>
