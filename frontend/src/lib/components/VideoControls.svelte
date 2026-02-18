<script>
  import { formatVideoTime } from '../format.js'
  import { spriteUrl } from '../api.js'
  import EventTimeline from './EventTimeline.svelte'

  /**
   * Video timeline scrubber with filmstrip background, event overlay,
   * playback controls, speed selector, and fullscreen toggle.
   *
   * Touch support: dragging on the timeline works with both mouse and touch.
   */

  /** @type {{ route: object, currentTime: number, duration: number, events?: Array, durationMs?: number, onSeek: (t: number) => void, onToggle: () => void, onRate: (r: number) => void, isPlaying?: boolean }} */
  let {
    route,
    currentTime = 0,
    duration = 0,
    events = [],
    durationMs = 0,
    onSeek,
    onToggle,
    onRate,
    isPlaying = false,
  } = $props()

  let timelineEl = $state(null)
  let isDragging = $state(false)
  let showSpeedMenu = $state(false)
  let playbackRate = $state(1)

  const speeds = [0.5, 1, 1.5, 2]
  const progress = $derived(duration > 0 ? (currentTime / duration) * 100 : 0)
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
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return pct * duration
  }

  function startDrag(e) {
    isDragging = true
    const t = getTimeFromEvent(e)
    onSeek(t)

    // Prevent text selection during drag
    e.preventDefault()
  }

  function onDrag(e) {
    if (!isDragging) return
    const t = getTimeFromEvent(e)
    onSeek(t)
  }

  function endDrag() {
    isDragging = false
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
  <!-- Timeline scrubber -->
  <div
    bind:this={timelineEl}
    class="relative h-10 rounded-lg overflow-hidden cursor-pointer select-none group"
    role="slider"
    tabindex="0"
    aria-label="Video timeline"
    aria-valuenow={Math.round(currentTime)}
    aria-valuemin={0}
    aria-valuemax={Math.round(duration)}
    onmousedown={startDrag}
    ontouchstart={startDrag}
    onkeydown={(e) => {
      if (e.key === 'ArrowRight') onSeek(Math.min(duration, currentTime + 5))
      else if (e.key === 'ArrowLeft') onSeek(Math.max(0, currentTime - 5))
    }}
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

    <!-- Dark overlay for played region contrast -->
    <div
      class="absolute inset-0 bg-black/30"
      style="clip-path: inset(0 {100 - progress}% 0 0)"
    ></div>

    <!-- Playhead marker -->
    <div
      class="absolute top-0 bottom-0 w-0.5 bg-white shadow-[0_0_4px_rgba(255,255,255,0.5)]"
      style="left: {progress}%"
    >
      <div class="absolute -top-1 left-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full bg-white shadow-md"></div>
    </div>

    <!-- Segment number tooltip (visible on hover/drag) -->
    {#if isDragging || false}
      <div
        class="absolute -top-7 bg-surface-800 text-xs text-surface-200 px-1.5 py-0.5 rounded pointer-events-none"
        style="left: {progress}%; transform: translateX(-50%)"
      >
        Seg {currentSeg}
      </div>
    {/if}
  </div>

  <!-- Controls row -->
  <div class="flex items-center gap-2">
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

    <!-- Time display -->
    <span class="text-xs text-surface-300 font-mono tabular-nums min-w-[7ch]">
      {formatVideoTime(currentTime)}
    </span>
    <span class="text-xs text-surface-500">/</span>
    <span class="text-xs text-surface-400 font-mono tabular-nums min-w-[7ch]">
      {formatVideoTime(duration)}
    </span>

    <!-- Segment indicator -->
    <span class="text-xs text-surface-500 ml-1">
      seg {currentSeg}
    </span>

    <div class="flex-1"></div>

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
