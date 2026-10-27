<script>
  import { createClip, fetchRouteClips, clipEstimateMB, CLIP_MAX_SEC, CLIP_BITRATES, CLIP_DEFAULT_BITRATE } from '../api.js'
  import { formatVideoTime } from '../format.js'

  /** @type {{ open: boolean, routeName: string, startSec: number, endSec: number, routeDurationSec: number, currentTime?: number, onClose?: () => void, onCreated?: (clips: any[]) => void }} */
  let {
    open = $bindable(false),
    routeName = '',
    startSec = 0,
    endSec = 0,
    routeDurationSec = 0,
    currentTime = 0,
    onClose,
    onCreated,
  } = $props()

  let dialogEl = $state(null)

  // Window being clipped (starts from props on each open; may be shortened by the user)
  let winStart = $state(0)
  let winEnd = $state(0)
  let picked = $state(false) // user picked a shortening option for a too-long window

  const cameraOptions = [
    { key: 'road', label: 'Road' },
    { key: 'wide', label: 'Wide' },
    { key: 'driver', label: 'Driver' },
  ]
  let selected = $state({ road: true, wide: true, driver: false })
  let bitrate = $state(CLIP_DEFAULT_BITRATE)
  let name = $state('')

  let availability = $state(null) // { cameras, supported, free_bytes }
  let loadError = $state('')
  let submitting = $state(false)
  let error = $state('')

  // Snap bounds to 0.1 s so float noise never pushes a 2-minute window over the limit
  const snap = (x) => Math.round(x * 10) / 10

  const length = $derived(winEnd - winStart)
  const tooLong = $derived(length > CLIP_MAX_SEC + 1e-6 && !picked)
  const tooShort = $derived(length < 1)
  const supported = $derived(availability?.supported !== false)

  function covered(key) {
    const ranges = availability?.cameras?.[key]
    if (!ranges) return availability == null // unknown until loaded
    return ranges.some(([s, e]) => s <= winStart && e >= winEnd)
  }

  const chosenCameras = $derived(
    cameraOptions.filter(c => selected[c.key] && covered(c.key)).map(c => c.key)
  )
  const estimateMB = $derived(Math.round(clipEstimateMB(Math.max(0, length), bitrate)))
  const freeGB = $derived(availability?.free_bytes != null ? (availability.free_bytes / 1e9).toFixed(1) : null)

  const canCreate = $derived(
    !submitting && supported && !tooLong && !tooShort && chosenCameras.length > 0
  )

  // Reset and load availability each time the dialog opens
  $effect(() => {
    if (!open) return
    winStart = snap(startSec)
    winEnd = snap(endSec)
    picked = false
    error = ''
    loadError = ''
    name = ''
    availability = null
    const rn = routeName
    fetchRouteClips(rn)
      .then(a => { if (open && rn === routeName) availability = a })
      .catch(e => { loadError = e.message })
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

  function pickFirst() {
    winEnd = snap(winStart + CLIP_MAX_SEC)
    picked = true
  }

  function pickAroundPlayhead() {
    const lo = Math.max(0, currentTime - CLIP_MAX_SEC / 2)
    const hi = Math.min(routeDurationSec || currentTime + CLIP_MAX_SEC / 2, currentTime + CLIP_MAX_SEC / 2)
    winStart = snap(lo)
    winEnd = snap(hi)
    picked = true
  }

  async function handleCreate() {
    if (!canCreate) return
    submitting = true
    error = ''
    try {
      const res = await createClip(routeName, {
        startSec: winStart,
        endSec: Math.min(winEnd, winStart + CLIP_MAX_SEC),
        cameras: chosenCameras,
        bitrateMbps: bitrate,
        name: name.trim() || undefined,
      })
      onCreated?.(res.clips)
      handleClose()
    } catch (e) {
      error = e.message
    } finally {
      submitting = false
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialogEl}
  class="bg-surface-800 text-surface-100 rounded-xl border border-surface-700 shadow-2xl p-0 backdrop:bg-black/60 max-w-md w-full"
  onclose={handleClose}
>
  <div class="p-5">
    <h3 class="text-lg font-semibold mb-4">Dashcam Clip</h3>

    <!-- Window -->
    <div class="space-y-2 mb-4">
      <p class="text-xs text-surface-400 uppercase tracking-wider">Window</p>
      <p class="text-sm font-mono">
        {formatVideoTime(winStart)} – {formatVideoTime(winEnd)} ({Math.round(length)} s)
      </p>
      {#if tooLong}
        <p class="text-sm text-amber-400">Window is longer than 2 minutes</p>
        <div class="flex gap-2">
          <button class="btn-ghost" onclick={pickFirst}>First 2 min</button>
          <button class="btn-ghost" onclick={pickAroundPlayhead}>Around playhead</button>
        </div>
      {:else if tooShort}
        <p class="text-sm text-amber-400">Select at least 1 second</p>
      {/if}
    </div>

    <!-- Cameras -->
    <div class="space-y-2 mb-4">
      <p class="text-xs text-surface-400 uppercase tracking-wider">Cameras</p>
      {#each cameraOptions as cam}
        {@const ok = covered(cam.key)}
        <label class="flex items-center gap-3 hover:bg-surface-700/50 rounded-lg px-2 py-1.5 -mx-2 transition-colors {ok ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}">
          <input type="checkbox" bind:checked={selected[cam.key]} disabled={!ok} class="accent-engage-blue w-4 h-4" />
          <span class="text-sm">{cam.label}</span>
          {#if !ok}
            <span class="text-xs text-surface-400 ml-auto">not recorded for this window</span>
          {/if}
        </label>
      {/each}
    </div>

    <!-- Quality -->
    <div class="space-y-2 mb-4">
      <p class="text-xs text-surface-400 uppercase tracking-wider">Quality</p>
      <div class="flex items-center gap-4">
        {#each CLIP_BITRATES as b}
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="radio" bind:group={bitrate} value={b} class="accent-engage-blue" />
            <span class="text-sm">{b} Mbps</span>
          </label>
        {/each}
      </div>
      <p class="text-xs text-surface-400">
        ~{estimateMB} MB per camera{#if freeGB != null} · {freeGB} GB free{/if}
      </p>
    </div>

    <!-- Name -->
    <div class="space-y-2 mb-4">
      <p class="text-xs text-surface-400 uppercase tracking-wider">Name (optional)</p>
      <input
        type="text"
        bind:value={name}
        placeholder="route date, camera, window"
        class="w-full bg-surface-700 border border-surface-600 rounded px-2 py-1.5 text-sm"
      />
    </div>

    {#if !supported}
      <p class="text-sm text-amber-400 mb-3">Clip encoding needs openpilot 0.11.2 on this device</p>
    {/if}
    {#if loadError}
      <p class="text-sm text-red-400 mb-3">{loadError}</p>
    {/if}
    {#if error}
      <p class="text-sm text-red-400 mb-3">{error}</p>
    {/if}

    <!-- Actions -->
    <div class="flex justify-end gap-2">
      <button class="btn-ghost" onclick={handleClose}>Cancel</button>
      <button class="btn-primary" onclick={handleCreate} disabled={!canCreate}>
        {submitting ? 'Creating…' : 'Create'}
      </button>
    </div>
  </div>
</dialog>
