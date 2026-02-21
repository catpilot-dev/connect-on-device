<script>
  import { formatVideoTime, formatAbsoluteTime, formatAbsoluteTimeHM } from '../format.js'
  import { spriteUrl, frameUrl } from '../api.js'
  import EventTimeline from './EventTimeline.svelte'

  /**
   * Video timeline scrubber with filmstrip background, event overlay,
   * playback controls, speed selector, and fullscreen toggle.
   *
   * Touch support: dragging on the timeline works with both mouse and touch.
   */

  /** @type {{ route: object, currentTime: number, duration: number, events?: Array, durationMs?: number, startTime?: number, onSeek: (t: number) => void, onToggle: () => void, onRate: (r: number) => void, onScreenshot?: () => void, onStepFrame?: (delta: number) => void, isPlaying?: boolean, screenshotBusy?: boolean, selectionStart?: number, selectionEnd?: number }} */
  let {
    route,
    currentTime = 0,
    duration = 0,
    events = [],
    durationMs = 0,
    startTime = 0,
    onSeek,
    onToggle,
    onRate,
    onScreenshot,
    onStepFrame,
    isPlaying = false,
    screenshotBusy = false,
    selectionStart = $bindable(0),
    selectionEnd = $bindable(0),
  } = $props()

  let timelineEl = $state(null)
  let isDragging = $state(false)
  let draggingHandle = $state(null) // 'start' | 'end' | null
  let dragOrigin = $state(null) // time where bar drag started
  let prevSel = { start: 0, end: 0 } // selection before bar drag
  let showSpeedMenu = $state(false)
  let playbackRate = $state(1)
  let viewStart = $state(0)
  let viewEnd = $state(0)

  // Initialize selEnd and viewEnd to full duration when it becomes known
  $effect(() => {
    if (duration > 0 && selectionEnd === 0) {
      selectionEnd = duration
    }
    if (duration > 0 && viewEnd === 0) {
      viewEnd = duration
    }
  })

  const speeds = [0.5, 1, 1.5, 2]
  const GUTTER = 5 // percent reserved on each side for handles
  const INNER = 100 - 2 * GUTTER // 90% for filmstrip
  const viewDur = $derived(viewEnd - viewStart)
  // All percentages are view-relative: 0% = viewStart, 100% = viewEnd
  const progress = $derived(viewDur > 0 ? Math.max(0, Math.min(100, ((currentTime - viewStart) / viewDur) * 100)) : 0)
  const selStartPct = $derived(viewDur > 0 ? Math.max(0, Math.min(100, ((selectionStart - viewStart) / viewDur) * 100)) : 0)
  const selEndPct = $derived(viewDur > 0 ? Math.max(0, Math.min(100, ((selectionEnd - viewStart) / viewDur) * 100)) : 0)
  // Handle positions mapped to outer container coordinates
  const handleStartLeft = $derived(GUTTER + selStartPct * INNER / 100)
  const handleEndLeft = $derived(GUTTER + selEndPct * INNER / 100)
  const playheadLeft = $derived(GUTTER + progress * INNER / 100)
  // Filmstrip zoom: scale and translate to show only the view window
  const filmScaleX = $derived(viewDur > 0 && duration > 0 ? duration / viewDur : 1)
  const filmTranslateX = $derived(duration > 0 ? -(viewStart / duration) * 100 : 0)
  const hasAbsTime = $derived(!!formatAbsoluteTime(startTime, 0))
  const currentSeg = $derived(Math.floor(currentTime / 60))
  const totalSegs = $derived(route?.maxqlog != null ? route.maxqlog + 1 : Math.ceil(duration / 60))

  // Filmstrip segments for timeline background
  const filmstripSegs = $derived(
    Array.from({ length: totalSegs }, (_, i) => i)
  )

  function getTimeFromEvent(e) {
    if (!timelineEl || duration <= 0) return 0
    const rect = timelineEl.getBoundingClientRect()
    const clientX = e.touches ? e.touches[0].clientX : e.clientX
    const pct = (clientX - rect.left) / rect.width
    if (draggingHandle) {
      // Allow extrapolation beyond view so handles can expand the selection
      return Math.max(0, Math.min(duration, viewStart + pct * viewDur))
    }
    return viewStart + Math.max(0, Math.min(1, pct)) * viewDur
  }

  function startHandleDrag(handle, e) {
    draggingHandle = handle
    isDragging = true
    e.preventDefault()
    e.stopPropagation()
  }

  function startDrag(e) {
    isDragging = true
    const t = getTimeFromEvent(e)
    dragOrigin = t
    prevSel = { start: selectionStart, end: selectionEnd }
    onSeek(t)
    e.preventDefault()
  }

  function onDrag(e) {
    if (!isDragging) return
    const t = getTimeFromEvent(e)
    if (draggingHandle === 'start') {
      selectionStart = Math.max(0, Math.min(t, selectionEnd - 1))
      if (selectionStart < viewStart) viewStart = selectionStart
      onSeek(selectionStart)
    } else if (draggingHandle === 'end') {
      selectionEnd = Math.min(duration, Math.max(t, selectionStart + 1))
      if (selectionEnd > viewEnd) viewEnd = selectionEnd
      onSeek(selectionStart)
    } else if (dragOrigin != null) {
      // Bar drag-to-select: create selection from drag origin to current
      const lo = Math.max(0, Math.min(dragOrigin, t))
      const hi = Math.min(duration, Math.max(dragOrigin, t))
      if (hi - lo > 5) {
        selectionStart = lo
        selectionEnd = hi
      }
      onSeek(t)
    }
  }

  function endDrag() {
    const selDur = selectionEnd - selectionStart
    if (draggingHandle && selDur > 0) {
      // Handle drag → zoom to selection
      const padding = selDur / 2
      viewStart = Math.max(0, selectionStart - padding)
      viewEnd = Math.min(duration, selectionEnd + padding)
    } else if (dragOrigin != null && selDur > 5 && selDur < duration - 5) {
      // Bar drag created a meaningful selection → zoom
      const padding = selDur / 2
      viewStart = Math.max(0, selectionStart - padding)
      viewEnd = Math.min(duration, selectionEnd + padding)
    } else if (dragOrigin != null && selDur <= 5) {
      // Tap or micro-drag: restore previous selection
      selectionStart = prevSel.start
      selectionEnd = prevSel.end
    }
    isDragging = false
    draggingHandle = null
    dragOrigin = null
  }

  // Global mouse/touch listeners for dragging outside timeline
  $effect(() => {
    if (isDragging) {
      const moveHandler = (e) => onDrag(e)
      const upHandler = () => endDrag()

      window.addEventListener('mousemove', moveHandler)
      window.addEventListener('mouseup', upHandler)
      window.addEventListener('touchmove', moveHandler, { passive: true })
      window.addEventListener('touchend', upHandler)
      window.addEventListener('touchcancel', upHandler)

      return () => {
        window.removeEventListener('mousemove', moveHandler)
        window.removeEventListener('mouseup', upHandler)
        window.removeEventListener('touchmove', moveHandler)
        window.removeEventListener('touchend', upHandler)
        window.removeEventListener('touchcancel', upHandler)
      }
    }
  })

  function setSpeed(rate) {
    playbackRate = rate
    onRate(rate)
    showSpeedMenu = false
  }

  function resetView() {
    selectionStart = 0
    selectionEnd = duration
    viewStart = 0
    viewEnd = duration
  }

  function toggleFullscreen() {
    const container = timelineEl?.closest('[data-video-container]')
    if (!container) return
    if (document.fullscreenElement) {
      document.exitFullscreen?.()
    } else {
      // Fullscreen API varies by browser
      const rfs = container.requestFullscreen
        || container.webkitRequestFullscreen  // Safari
        || container.mozRequestFullScreen     // Firefox (legacy)
        || container.msRequestFullscreen      // IE/Edge legacy
      rfs?.call(container)
    }
  }
</script>

<div class="space-y-2">
  <!-- Timeline scrubber: outer wrapper for handles + inner filmstrip -->
  <div class="relative h-10 select-none group">
    <!-- Inner timeline (90% width, centered) -->
    <div
      bind:this={timelineEl}
      class="absolute inset-y-0 rounded-lg overflow-hidden cursor-pointer"
      style="left: {GUTTER}%; right: {GUTTER}%"
      role="slider"
      tabindex="0"
      aria-label="Video timeline"
      aria-valuenow={Math.round(currentTime)}
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      onmousedown={startDrag}
      ontouchstart={startDrag}
      ondblclick={resetView}
      onkeydown={(e) => {
        if (e.key === 'ArrowRight') onSeek(Math.min(duration, currentTime + 5))
        else if (e.key === 'ArrowLeft') onSeek(Math.max(0, currentTime - 5))
      }}
    >
      <!-- Filmstrip + events (zoomed with view window) -->
      <div
        class="absolute inset-0"
        style="transform-origin: 0 0; transform: scaleX({filmScaleX}) translateX({filmTranslateX}%){isDragging ? '' : '; transition: transform 0.3s ease-out'}"
      >
        <!-- Filmstrip background -->
        <div class="absolute inset-0 flex opacity-40">
          {#each filmstripSegs as seg}
            <div class="flex-1 min-w-0 overflow-hidden">
              <img
                src={spriteUrl(route, seg)}
                alt=""
                class="w-full h-full object-cover"
                loading="lazy"
                onerror={(e) => e.target.style.visibility = 'hidden'}
              />
            </div>
          {/each}
        </div>

        <!-- Event timeline overlay -->
        <div class="absolute bottom-0 left-0 right-0">
          <EventTimeline {events} {durationMs} height="3px" />
        </div>
      </div>

      <!-- Dark overlay for played region contrast -->
      <div
        class="absolute inset-0 bg-black/30"
        style="clip-path: inset(0 {100 - progress}% 0 0)"
      ></div>

      <!-- Selection: darken outside range -->
      {#if duration > 0}
        <div
          class="absolute top-0 bottom-0 left-0 bg-black/50 pointer-events-none"
          style="width: {selStartPct}%"
        ></div>
        <div
          class="absolute top-0 bottom-0 right-0 bg-black/50 pointer-events-none"
          style="width: {100 - selEndPct}%"
        ></div>
      {/if}

      <!-- Playhead marker -->
      <div
        class="absolute top-0 bottom-0 w-0.5 bg-white shadow-[0_0_4px_rgba(255,255,255,0.5)] z-20"
        style="left: {progress}%"
      ></div>
    </div>

    <!-- Selection handles in outer gutter area -->
    {#if duration > 0}
      <!-- Start handle: > tip at selection boundary -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute top-0 bottom-0 w-4 cursor-ew-resize z-10 flex items-center justify-end"
        style="left: {handleStartLeft}%; transform: translateX(-100%)"
        onmousedown={(e) => startHandleDrag('start', e)}
        ontouchstart={(e) => startHandleDrag('start', e)}
      >
        <svg class="w-3 h-5 drop-shadow" viewBox="0 0 6 12" fill="white">
          <path d="M0 0 L6 6 L0 12 Z"/>
        </svg>
      </div>

      <!-- End handle: < tip at selection boundary -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute top-0 bottom-0 w-4 cursor-ew-resize z-10 flex items-center justify-start"
        style="left: {handleEndLeft}%"
        onmousedown={(e) => startHandleDrag('end', e)}
        ontouchstart={(e) => startHandleDrag('end', e)}
      >
        <svg class="w-3 h-5 drop-shadow" viewBox="0 0 6 12" fill="white">
          <path d="M6 0 L0 6 L6 12 Z"/>
        </svg>
      </div>
    {/if}

    <!-- Segment number above playhead (in outer container, not clipped) -->
    <div
      class="absolute -top-3 text-[10px] text-surface-300 font-mono pointer-events-none z-20"
      style="left: {playheadLeft}%; transform: translateX(-50%)"
    >{currentSeg}</div>
  </div>

  <!-- Controls row -->
  <div class="flex items-center gap-2">
    <!-- Frame step back -->
    <button
      class="btn-ghost p-1.5 disabled:opacity-30"
      onclick={() => onStepFrame?.(-1)}
      disabled={isPlaying}
      aria-label="Previous frame"
    >
      <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
        <path d="M15.7 17.16a1.5 1.5 0 002.3-1.27V4.11a1.5 1.5 0 00-2.3-1.27L6.36 8.73a1.5 1.5 0 000 2.54l9.34 5.89z"/>
        <rect x="2" y="3" width="2.5" height="14" rx="0.75"/>
      </svg>
    </button>

    <!-- Play/Pause -->
    <button
      class="btn-ghost p-2"
      onclick={onToggle}
      aria-label={isPlaying ? 'Pause' : 'Play'}
    >
      {#if isPlaying}
        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
          <path fill-rule="evenodd" d="M5.75 3a.75.75 0 01.75.75v12.5a.75.75 0 01-1.5 0V3.75A.75.75 0 015.75 3zm8.5 0a.75.75 0 01.75.75v12.5a.75.75 0 01-1.5 0V3.75a.75.75 0 01.75-.75z" clip-rule="evenodd"/>
        </svg>
      {:else}
        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
          <path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z"/>
        </svg>
      {/if}
    </button>

    <!-- Frame step forward -->
    <button
      class="btn-ghost p-1.5 disabled:opacity-30"
      onclick={() => onStepFrame?.(1)}
      disabled={isPlaying}
      aria-label="Next frame"
    >
      <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
        <path d="M4.3 2.84A1.5 1.5 0 002 4.11v11.78a1.5 1.5 0 002.3 1.27l9.34-5.89a1.5 1.5 0 000-2.54L4.3 2.84z"/>
        <rect x="15.5" y="3" width="2.5" height="14" rx="0.75"/>
      </svg>
    </button>

    <!-- Time display: [abs_start  +playhead  +end] -->
    <span class="text-xs font-mono tabular-nums">
      <span class="text-surface-400">{hasAbsTime ? formatAbsoluteTimeHM(startTime, selectionStart) : formatVideoTime(selectionStart)}</span>
      <span class="text-surface-300 mx-1">+{formatVideoTime(currentTime - selectionStart)}</span>
      <span class="text-surface-400">+{formatVideoTime(selectionEnd - selectionStart)}</span>
    </span>

    <!-- Frame URL: open in new tab -->
    <a
      href={frameUrl(route.fullname, currentTime)}
      target="_blank"
      rel="noopener"
      class="btn-ghost p-1.5 text-surface-400 hover:text-surface-200"
      title="Open frame in new tab"
    >
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"/>
      </svg>
    </a>

    <div class="flex-1"></div>

    <!-- Screenshot -->
    {#if onScreenshot}
      <button
        class="btn-ghost p-2 disabled:opacity-30"
        onclick={onScreenshot}
        disabled={isPlaying || screenshotBusy}
        aria-label="Screenshot"
      >
        {#if screenshotBusy}
          <div class="w-4 h-4 border-2 border-surface-300 border-t-transparent rounded-full animate-spin"></div>
        {:else}
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
        {/if}
      </button>
    {/if}

    <!-- Speed selector -->
    <div class="relative">
      <button
        class="btn-ghost text-xs px-2 py-1"
        onclick={() => showSpeedMenu = !showSpeedMenu}
      >
        {playbackRate}x
      </button>
      {#if showSpeedMenu}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_interactive_supports_focus -->
        <div
          class="absolute bottom-full right-0 mb-1 bg-surface-800 border border-surface-700 rounded-lg shadow-xl py-1 z-20"
          role="menu"
          onclick={(e) => e.stopPropagation()}
        >
          {#each speeds as rate}
            <button
              class="block w-full text-left px-4 py-1.5 text-xs hover:bg-surface-700 transition-colors"
              class:text-engage-blue={rate === playbackRate}
              role="menuitem"
              onclick={() => setSpeed(rate)}
            >
              {rate}x
            </button>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Fullscreen -->
    <button
      class="btn-ghost p-2"
      onclick={toggleFullscreen}
      aria-label="Toggle fullscreen"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/>
      </svg>
    </button>
  </div>
</div>

<!-- Close speed menu on outside click -->
{#if showSpeedMenu}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="fixed inset-0 z-10" onclick={() => showSpeedMenu = false}></div>
{/if}
