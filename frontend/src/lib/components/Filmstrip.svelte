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
      imageCount = Math.max(2, Math.floor(width / 40))
    })
    ro.observe(container)
    return () => ro.disconnect()
  })

  // Distribute thumbnails evenly across route duration, regardless of segment count
  const slots = $derived(() => {
    const totalSegs = maxSegment + 1
    const duration = totalSegs * 60 // approximate duration in seconds
    const step = duration / imageCount
    return Array.from({ length: imageCount }, (_, i) => {
      const t = i * step + step / 2
      const seg = Math.min(Math.floor(t / 60), totalSegs - 1)
      const secInSeg = Math.floor(t % 60)
      return { seg, t: secInSeg }
    })
  })
</script>

<div bind:this={container} class="flex overflow-hidden rounded-t-lg h-10">
  {#each slots() as slot}
    <button
      class="flex-1 min-w-0 h-full bg-surface-700 overflow-hidden cursor-pointer hover:opacity-80 transition-opacity"
      onclick={() => onclick?.(slot.seg)}
    >
      <img
        src={spriteUrl(route, slot.seg, slot.t)}
        alt=""
        class="w-full h-full object-cover"
        loading="lazy"
        onerror={(e) => e.target.style.display = 'none'}
      />
    </button>
  {/each}
</div>
