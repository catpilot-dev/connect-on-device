<script>
  import { onMount, onDestroy } from 'svelte'
  import { fetchClips, clipUrl, deleteClip } from '../api.js'
  import { dongleId, selectedRoute } from '../stores.js'
  import { formatBytes, formatVideoTime } from '../format.js'
  import { createPoll } from '../utils/poll.js'

  const FAST_MS = 2000
  const SLOW_MS = 15000

  let clips = $state([])
  let totalBytes = $state(0)
  let supported = $state(true)
  let loading = $state(true)
  let error = $state(null)
  let playing = $state(null)
  let now = $state(Date.now() / 1000)

  let busy = $derived(clips.some(c => c.status === 'queued' || c.status === 'encoding'))
  let encoding = $derived(clips.some(c => c.status === 'encoding'))

  async function load(initial = false) {
    if (initial) loading = true
    try {
      const data = await fetchClips()
      clips = data.clips ?? []
      totalBytes = data.total_bytes ?? 0
      supported = data.supported !== false
      error = null
    } catch (e) {
      if (initial || clips.length === 0) error = e.message
    } finally {
      loading = false
    }
  }

  let poll = null
  let pollMs = 0
  let ticker = null

  // Restart the poll when the fast/slow condition changes.
  $effect(() => {
    const ms = busy ? FAST_MS : SLOW_MS
    if (ms === pollMs) return
    pollMs = ms
    poll?.stop()
    poll = createPoll(load, ms)
    poll.start()
  })

  // Tick `now` once a second while anything is encoding.
  $effect(() => {
    if (encoding) {
      now = Date.now() / 1000
      ticker = setInterval(() => { now = Date.now() / 1000 }, 1000)
      return () => { clearInterval(ticker); ticker = null }
    }
  })

  onMount(() => load(true))
  onDestroy(() => { poll?.stop(); if (ticker) clearInterval(ticker) })

  async function remove(c) {
    const msg = c.status === 'encoding'
      ? 'Stop encoding and delete this clip?'
      : `Delete clip ${c.filename}?`
    if (!confirm(msg)) return
    try {
      await deleteClip(c.filename)
    } catch (e) {
      error = e.message
      return
    }
    if (playing === c.filename) playing = null
    await load()
  }

  function openRoute(c) {
    history.pushState(null, '', `/${$dongleId}/${c.local_id}/${Math.round(c.start_sec)}/${Math.round(c.end_sec)}`)
    selectedRoute.set(c.local_id)
  }

  function range(c) {
    return `${formatVideoTime(c.start_sec)}–${formatVideoTime(c.end_sec)}`
  }
</script>

<div class="p-4 max-w-4xl mx-auto">
  <div class="flex items-center justify-between mb-4">
    <h2 class="text-lg font-medium text-surface-100">
      Clips
      {#if !loading && clips.length > 0}
        <span class="text-surface-500 text-sm ml-2">({clips.length}) · {formatBytes(totalBytes)}</span>
      {/if}
    </h2>
    <button class="btn-ghost text-xs" onclick={() => load()}>Refresh</button>
  </div>

  {#if !supported}
    <div class="mb-4 px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-sm text-surface-300">
      Clip encoding needs openpilot 0.11.2 on this device
    </div>
  {/if}

  {#if loading}
    <div class="flex items-center justify-center h-48">
      <p class="text-surface-400 text-sm">Loading...</p>
    </div>
  {:else if error && clips.length === 0}
    <div class="flex items-center justify-center h-48">
      <p class="text-engage-red text-sm">{error}</p>
    </div>
  {:else if clips.length === 0}
    <div class="flex items-center justify-center h-48">
      <div class="text-center">
        <p class="text-surface-400">No clips yet</p>
        <p class="text-surface-600 text-sm mt-1">Select a window on a route and press Clip</p>
      </div>
    </div>
  {:else}
    {#if error}<p class="text-engage-red text-xs mb-2">{error}</p>{/if}
    <div class="flex flex-col gap-2">
      {#each clips as c (c.filename)}
        <div class="rounded-lg bg-surface-800 border border-surface-700 px-3 py-2.5">
          <div class="flex items-center gap-x-3 gap-y-1 flex-wrap text-sm">
            <span class="text-surface-100 truncate max-w-full">{c.filename}</span>
            <button class="text-xs text-surface-400 hover:text-surface-100 underline cursor-pointer" onclick={() => openRoute(c)}>
              {c.route}
            </button>
            <span class="text-xs text-surface-400">{c.camera}</span>
            <span class="text-xs text-surface-400">{range(c)}</span>
            {#if c.size != null}
              <span class="text-xs text-surface-500">{formatBytes(c.size)}</span>
            {/if}

            <div class="ml-auto flex items-center gap-2 text-xs">
              {#if c.status === 'queued'}
                <span class="text-surface-400">queued</span>
              {:else if c.status === 'encoding'}
                <span class="text-surface-300">encoding… {formatVideoTime(Math.max(0, now - (c.started_at ?? now)))} of ~{formatVideoTime(c.eta_sec ?? 0)}</span>
              {:else if c.status === 'error'}
                <span class="text-engage-red">error: {c.error}</span>
              {:else if c.status === 'ready'}
                <button class="btn-ghost text-xs" onclick={() => { playing = playing === c.filename ? null : c.filename }}>
                  {playing === c.filename ? 'Hide' : 'Play'}
                </button>
                <a class="btn-ghost text-xs" href={clipUrl(c.filename)} download={c.filename}>Download</a>
              {/if}
              <button class="btn-ghost text-xs text-engage-red" onclick={() => remove(c)}>
                {c.status === 'encoding' ? 'Cancel' : 'Delete'}
              </button>
            </div>
          </div>
          {#if playing === c.filename && c.status === 'ready'}
            <!-- svelte-ignore a11y_media_has_caption -->
            <video class="mt-2 w-full rounded bg-black" controls preload="metadata" src={clipUrl(c.filename)}></video>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>
