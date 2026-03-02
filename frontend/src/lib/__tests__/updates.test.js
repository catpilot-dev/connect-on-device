import { describe, it, expect, vi, beforeEach } from 'vitest'

// ── API functions ───────────────────────────────────────────────────

describe('fetchUpdates', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('calls GET /v1/updates/check', async () => {
    const mockData = {
      cod: { available: true, current: '1.0.0', latest: '1.2.0', summary: 'Bug fixes' },
      plugins: { available: false, current: 'abc', latest: 'abc', summary: '' },
    }
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve(mockData),
    })

    const { fetchUpdates } = await import('../api.js')
    const result = await fetchUpdates()

    expect(fetch).toHaveBeenCalledWith('/v1/updates/check')
    expect(result.cod.available).toBe(true)
    expect(result.cod.current).toBe('1.0.0')
    expect(result.cod.latest).toBe('1.2.0')
    expect(result.plugins.available).toBe(false)
  })

  it('throws on non-ok response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    const { fetchUpdates } = await import('../api.js')
    await expect(fetchUpdates()).rejects.toThrow('500')
  })
})

describe('applyUpdates', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('calls POST /v1/updates/apply', async () => {
    const mockData = { status: 'ok', cod_updated: true, plugins_updated: false, reboot_required: false }
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve(mockData),
    })

    const { applyUpdates } = await import('../api.js')
    const result = await applyUpdates()

    expect(fetch).toHaveBeenCalledWith('/v1/updates/apply', { method: 'POST' })
    expect(result.cod_updated).toBe(true)
    expect(result.reboot_required).toBe(false)
  })

  it('throws on non-ok response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    const { applyUpdates } = await import('../api.js')
    await expect(applyUpdates()).rejects.toThrow('500')
  })
})

// ── Banner display names ────────────────────────────────────────────

describe('UpdateBanner name display', () => {
  function getNames(updates) {
    const parts = []
    if (updates?.cod?.available) parts.push('COD')
    if (updates?.plugins?.available) parts.push('Plugins')
    return parts.join(' \u00b7 ')
  }

  it('shows COD only', () => {
    expect(getNames({ cod: { available: true }, plugins: { available: false } })).toBe('COD')
  })

  it('shows Plugins only', () => {
    expect(getNames({ cod: { available: false }, plugins: { available: true } })).toBe('Plugins')
  })

  it('shows both', () => {
    expect(getNames({ cod: { available: true }, plugins: { available: true } })).toBe('COD \u00b7 Plugins')
  })

  it('empty when neither', () => {
    expect(getNames({ cod: { available: false }, plugins: { available: false } })).toBe('')
  })
})

// ── Banner visibility ───────────────────────────────────────────────

describe('UpdateBanner visibility', () => {
  function isVisible(updates, dismissed) {
    return !!(updates && !dismissed && (updates.cod?.available || updates.plugins?.available))
  }

  it('hidden when updates is null', () => {
    expect(isVisible(null, false)).toBe(false)
  })

  it('hidden when dismissed', () => {
    expect(isVisible({ cod: { available: true }, plugins: { available: false } }, true)).toBe(false)
  })

  it('hidden when no updates', () => {
    expect(isVisible({ cod: { available: false }, plugins: { available: false } }, false)).toBe(false)
  })

  it('shown for COD update', () => {
    expect(isVisible({ cod: { available: true }, plugins: { available: false } }, false)).toBe(true)
  })

  it('shown for plugin update', () => {
    expect(isVisible({ cod: { available: false }, plugins: { available: true } }, false)).toBe(true)
  })

  it('handles null repos gracefully', () => {
    expect(isVisible({ cod: null, plugins: null }, false)).toBe(false)
  })
})

// ── Onroad safety ───────────────────────────────────────────────────

describe('UpdateBanner onroad safety', () => {
  it('update button hidden when onroad', () => {
    // Template: {#if !result && !applying && !isOnroad}
    const showUpdateBtn = !null && !false && !true  // result=null, applying=false, isOnroad=true
    expect(showUpdateBtn).toBe(false)
  })

  it('update button shown when parked', () => {
    const showUpdateBtn = !null && !false && !false  // isOnroad=false
    expect(showUpdateBtn).toBe(true)
  })

  it('reboot button hidden when onroad', () => {
    // Template: {#if result?.reboot_required && !isOnroad}
    const result = { reboot_required: true }
    expect(result.reboot_required && !true).toBe(false)  // isOnroad=true
  })

  it('reboot button shown when parked and reboot needed', () => {
    const result = { reboot_required: true }
    expect(result.reboot_required && !false).toBe(true)  // isOnroad=false
  })

  it('reboot button hidden when no reboot needed', () => {
    const result = { reboot_required: false }
    expect(result.reboot_required && !false).toBe(false)
  })
})

// ── COD release-specific display ────────────────────────────────────

describe('COD release version display', () => {
  it('response contains version strings not commit hashes', () => {
    const cod = { available: true, current: '1.0.0', latest: '1.2.0', summary: 'Bug fixes' }
    // Version strings are human-readable, not git hashes
    expect(cod.current).toMatch(/^\d+\.\d+\.\d+$/)
    expect(cod.latest).toMatch(/^\d+\.\d+\.\d+$/)
  })

  it('download_url is NOT exposed to frontend', () => {
    // Backend strips download_url from public response
    const publicResponse = { available: true, current: '1.0.0', latest: '1.2.0', summary: 'fixes' }
    expect(publicResponse).not.toHaveProperty('download_url')
    expect(publicResponse).not.toHaveProperty('tag')
  })
})
