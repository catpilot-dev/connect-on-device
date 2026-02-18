<script>
  import { spriteUrl } from '../api.js'

  /** @type {{ route: object, maxSegment: number, onclick?: (seg: number) => void }} */
  let { route, maxSegment = 0, onclick } = $props()

  let container = $state(null)
  let imageCount = $state(4)

  $effect(() => {
    if (!container) return
    const ro = new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width
      imageCount = Math.max(2, Math.floor(width / 80))
    })
    ro.observe(container)
    return () => ro.disconnect()
  })

  // Distribute thumbnails evenly across segments
  const segments = $derived(() => {
    const count = maxSegment + 1
    if (count <= imageCount) return Array.from({ length: count }, (_, i) => i)
    const step = (count - 1) / (imageCount - 1)
    return Array.from({ length: imageCount }, (_, i) => Math.round(i * step))
  })
</script>

<div bind:this={container} class="flex gap-0.5 overflow-hidden rounded-t-lg">
  {#each segments() as seg}
    <button
      class="flex-1 min-w-0 aspect-[16/9] bg-surface-700 overflow-hidden cursor-pointer hover:opacity-80 transition-opacity relative"
      onclick={() => onclick?.(seg)}
    >
      <span class="absolute inset-0 flex items-center justify-center text-[10px] text-surface-500">{seg}</span>
      <img
        src={spriteUrl(route, seg)}
        alt="Segment {seg}"
        class="w-full h-full object-cover relative"
        loading="lazy"
        onerror={(e) => e.target.style.display = 'none'}
      />
    </button>
  {/each}
</div>
