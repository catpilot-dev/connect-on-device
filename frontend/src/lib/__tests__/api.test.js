import { describe, it, expect } from 'vitest'
import { connectdataUrl, hudUrl, spriteUrl, downloadUrl } from '../api.js'

// ── connectdataUrl ──────────────────────────────────────────

describe('connectdataUrl', () => {
  it('with route.url field uses URL pathname', () => {
    const route = { url: 'http://localhost:8082/connectdata/abc/2025-01-01--12-00-00', fullname: 'abc/2025-01-01--12-00-00' }
    const result = connectdataUrl(route, 0, 'qcamera.ts')
    expect(result).toBe('/connectdata/abc/2025-01-01--12-00-00/0/qcamera.ts')
  })

  it('without route.url uses fullname', () => {
    const route = { fullname: 'abc/2025-01-01--12-00-00' }
    const result = connectdataUrl(route, 0, 'qcamera.ts')
    expect(result).toBe('/connectdata/abc/2025-01-01--12-00-00/0/qcamera.ts')
  })

  it('different segments and files', () => {
    const route = { fullname: 'dongle/date' }
    expect(connectdataUrl(route, 5, 'rlog.zst')).toBe('/connectdata/dongle/date/5/rlog.zst')
  })
})

// ── hudUrl ──────────────────────────────────────────────────

describe('hudUrl', () => {
  it('builds hud URL with offset', () => {
    const route = { fullname: 'abc/date' }
    const result = hudUrl(route, 2, 15000.7)
    expect(result).toBe('/connectdata/abc/date/2/hud?t=15001')
  })
})

// ── spriteUrl ───────────────────────────────────────────────

describe('spriteUrl', () => {
  it('builds sprite URL', () => {
    const route = { fullname: 'abc/date' }
    expect(spriteUrl(route, 3)).toBe('/connectdata/abc/date/3/sprite.jpg')
  })
})

// ── downloadUrl ─────────────────────────────────────────────

describe('downloadUrl', () => {
  it('default (rlog only, no segments)', () => {
    const result = downloadUrl('abc/date')
    expect(result).toBe('/v1/route/abc|date/download?files=rlog')
  })

  it('multiple file types with segments', () => {
    const result = downloadUrl('abc/date', ['rlog', 'qcamera'], [0, 1, 2])
    expect(result).toBe('/v1/route/abc|date/download?files=rlog,qcamera&segments=0,1,2')
  })

  it('no segments omits segment param', () => {
    const result = downloadUrl('abc/date', ['fcamera'], null)
    expect(result).toBe('/v1/route/abc|date/download?files=fcamera')
  })
})
