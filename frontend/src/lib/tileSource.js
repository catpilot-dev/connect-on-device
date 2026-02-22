/** Map tile source preference — persisted to localStorage */

const STORAGE_KEY = 'mapTileSource'
const DEFAULT = 'amap'

export const TILE_SOURCES = {
  amap: {
    label: 'AMap',
    desc: 'Gaode Maps (China)',
    url: 'https://wprd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
    subdomains: '1234',
    className: 'amap-dark',
    gcj02: true,  // needs WGS-84 → GCJ-02 transform
    maxZoom: 18,
  },
  cartodb: {
    label: 'CartoDB',
    desc: 'Dark Matter (overseas)',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd',
    className: '',
    gcj02: false,  // standard WGS-84
    maxZoom: 19,
  },
}

export function getTileSource() {
  if (typeof localStorage === 'undefined') return DEFAULT
  return localStorage.getItem(STORAGE_KEY) || DEFAULT
}

export function setTileSource(key) {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, key)
  }
}
