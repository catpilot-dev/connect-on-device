import { Route } from '../types'
import { DB } from './db'

export type NominatimResult = {
  display_name: string
  address: {
    road?: string
    neighbourhood?: string
    suburb?: string
    city?: string
    town?: string
    village?: string
    county?: string
    state?: string
    country?: string
  }
}

export const getTileUrl = () =>
  `https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png`

export const getPathStaticMapUrl = (
  _themeId: string,
  _coords: [number, number][],
  _width: number,
  _height: number,
  _hidpi: boolean,
): string | undefined => {
  return undefined
}

type Position = { lng?: number | null; lat?: number | null }

export const reverseGeocode = async ({ lng, lat }: Position): Promise<NominatimResult | undefined> => {
  if (!lng || !lat) return
  if (Math.abs(lng) < 0.001 && Math.abs(lat) < 0.001) return

  const db = await DB.init('geocode')
  const key = `${lng.toFixed(6)},${lat.toFixed(6)}`
  const saved = await db.get<NominatimResult>(key)
  if (saved) return saved

  try {
    const query = new URLSearchParams({
      lat: lat.toFixed(6),
      lon: lng.toFixed(6),
      format: 'json',
      zoom: '14',
    })
    const resp = await fetch(`https://nominatim.openstreetmap.org/reverse?${query.toString()}`, {
      headers: { 'User-Agent': 'connect-on-device/1.0' },
    })

    if (!resp.ok) {
      console.error(new Error(`Reverse geocode lookup failed: ${resp.status} ${resp.statusText}`))
      return
    }

    const result = (await resp.json()) as NominatimResult
    if (result) await db.set(key, result)
    return result
  } catch (error) {
    console.error('[geocode] Reverse geocode lookup failed', error)
    return
  }
}

export const getPlaceName = async (position: Position): Promise<string | undefined> => {
  const result = await reverseGeocode(position)
  if (!result) return
  const addr = result.address
  return (
    [
      addr.neighbourhood,
      addr.suburb,
      addr.city || addr.town || addr.village,
      addr.county,
      addr.state,
      addr.country,
    ].find(Boolean) || result.display_name?.split(',')[0] || ''
  )
}

export const getStartEndPlaceName = async (route: Route) => {
  const [start, end] = await Promise.all([
    getPlaceName({ lng: route.start_lng, lat: route.start_lat }),
    getPlaceName({ lng: route.end_lng, lat: route.end_lat }),
  ])
  return { start, end }
}
