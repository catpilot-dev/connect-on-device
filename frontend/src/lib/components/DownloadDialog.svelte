<script>
  import { downloadUrl } from '../api.js'

  /** @type {{ open: boolean, routeName: string, maxSegment: number, onClose: () => void }} */
  let {
    open = $bindable(false),
    routeName = '',
    maxSegment = 0,
    onClose,
  } = $props()

  let dialogEl = $state(null)

  // File type options
  const fileOptions = [
    { id: 'rlog', label: 'rlog.zst', desc: 'Full logs', checked: true },
    { id: 'qcamera', label: 'qcamera.ts', desc: 'Low-res video', checked: false },
    { id: 'fcamera', label: 'fcamera.hevc', desc: 'Road camera', checked: false },
    { id: 'ecamera', label: 'ecamera.hevc', desc: 'Wide camera', checked: false },
    { id: 'qlog', label: 'qlog.zst', desc: 'Quantized logs', checked: false },
  ]
  let selectedFiles = $state(fileOptions.map(f => ({ ...f })))

  // Segment selection
  let segMode = $state('all') // 'all' | 'range'
  let segFrom = $state(0)
  let segTo = $state(0)

  $effect(() => {
    segTo = maxSegment
  })

  $effect(() => {
    if (!dialogEl) return
    if (open && !dialogEl.open) dialogEl.showModal()
    else if (!open && dialogEl.open) dialogEl.close()
  })

  function handleClose() {
    open = false
    onClose?.()
  }

  function handleDownload() {
    const types = selectedFiles.filter(f => f.checked).map(f => f.id)
    if (types.length === 0) return

    let segments = null
    if (segMode === 'range') {
      const from = Math.max(0, Math.min(segFrom, maxSegment))
      const to = Math.max(from, Math.min(segTo, maxSegment))
      segments = Array.from({ length: to - from + 1 }, (_, i) => from + i)
    }

    const url = downloadUrl(routeName, types, segments)
    window.open(url, '_blank')
    handleClose()
  }

  const segCount = $derived(
    segMode === 'all'
      ? maxSegment + 1
      : Math.max(0, Math.min(segTo, maxSegment) - Math.max(0, segFrom) + 1)
  )
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialogEl}
  class="bg-surface-800 text-surface-100 rounded-xl border border-surface-700 shadow-2xl p-0 backdrop:bg-black/60 max-w-md w-full"
  onclose={handleClose}
>
  <div class="p-5">
    <h3 class="text-lg font-semibold mb-4">Download Route</h3>

    <!-- File types -->
    <div class="space-y-2 mb-4">
      <p class="text-xs text-surface-400 uppercase tracking-wider">File Types</p>
      {#each selectedFiles as file}
        <label class="flex items-center gap-3 cursor-pointer hover:bg-surface-700/50 rounded-lg px-2 py-1.5 -mx-2 transition-colors">
          <input type="checkbox" bind:checked={file.checked} class="accent-engage-blue w-4 h-4" />
          <span class="text-sm font-mono">{file.label}</span>
          <span class="text-xs text-surface-400 ml-auto">{file.desc}</span>
        </label>
      {/each}
    </div>

    <!-- Segment selection -->
    <div class="space-y-2 mb-5">
      <p class="text-xs text-surface-400 uppercase tracking-wider">Segments</p>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" bind:group={segMode} value="all" class="accent-engage-blue" />
          <span class="text-sm">All ({maxSegment + 1})</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" bind:group={segMode} value="range" class="accent-engage-blue" />
          <span class="text-sm">Range</span>
        </label>
      </div>
      {#if segMode === 'range'}
        <div class="flex items-center gap-2 ml-6">
          <input
            type="number"
            bind:value={segFrom}
            min="0"
            max={maxSegment}
            class="w-16 bg-surface-700 border border-surface-600 rounded px-2 py-1 text-sm text-center"
          />
          <span class="text-surface-400">to</span>
          <input
            type="number"
            bind:value={segTo}
            min="0"
            max={maxSegment}
            class="w-16 bg-surface-700 border border-surface-600 rounded px-2 py-1 text-sm text-center"
          />
          <span class="text-xs text-surface-400">({segCount} segs)</span>
        </div>
      {/if}
    </div>

    <!-- Actions -->
    <div class="flex justify-end gap-2">
      <button class="btn-ghost" onclick={handleClose}>Cancel</button>
      <button
        class="btn-primary"
        onclick={handleDownload}
        disabled={!selectedFiles.some(f => f.checked)}
      >
        Download
      </button>
    </div>
  </div>
</dialog>
