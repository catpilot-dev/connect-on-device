<script>
  let { value = 0, unit = '', thresholds = [], scale = 1, offset = 0, decimals = 0 } = $props()

  const displayValue = $derived(value * scale + offset)

  const color = $derived.by(() => {
    if (!thresholds.length) return '#e2e8f0'
    for (let i = thresholds.length - 1; i >= 0; i--) {
      if (displayValue >= thresholds[i].above) return thresholds[i].color
    }
    return thresholds[0]?.color ?? '#e2e8f0'
  })
</script>

<div class="digits-widget">
  <span class="digits-value" style="color: {color}">
    {displayValue.toFixed(decimals)}<span class="digits-unit">{unit}</span>
  </span>
</div>

<style>
  .digits-widget {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }
  .digits-value {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    font-size: clamp(3rem, 28cqi, 10rem);
    line-height: 1;
  }
  .digits-unit {
    font-size: 0.3em;
    color: var(--color-surface-500);
    margin-left: 2px;
    vertical-align: super;
  }
</style>
