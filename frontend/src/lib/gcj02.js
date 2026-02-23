/**
 * WGS-84 → GCJ-02 coordinate transformation.
 *
 * GCJ-02 is China's mandatory coordinate obfuscation system.
 * AMap/Gaode, Tencent Maps, and Google Maps China all use GCJ-02.
 * GPS hardware and OpenStreetMap use WGS-84.
 *
 * This transform adds ~300-600m offset so WGS-84 points align
 * with Chinese basemap tiles.
 */

const PI = Math.PI
const A = 6378245.0          // Semi-major axis (Krasovsky 1940)
const EE = 0.00669342162296  // Eccentricity squared

function transformLat(x, y) {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(y * PI) + 40.0 * Math.sin(y / 3.0 * PI)) * 2.0 / 3.0
  ret += (160.0 * Math.sin(y / 12.0 * PI) + 320.0 * Math.sin(y * PI / 30.0)) * 2.0 / 3.0
  return ret
}

function transformLon(x, y) {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(x * PI) + 40.0 * Math.sin(x / 3.0 * PI)) * 2.0 / 3.0
  ret += (150.0 * Math.sin(x / 12.0 * PI) + 300.0 * Math.sin(x / 30.0 * PI)) * 2.0 / 3.0
  return ret
}

/**
 * Convert WGS-84 to GCJ-02.
 * @param {number} lat - WGS-84 latitude
 * @param {number} lng - WGS-84 longitude
 * @returns {[number, number]} [gcj_lat, gcj_lng]
 */
export function wgs84ToGcj02(lat, lng) {
  let dLat = transformLat(lng - 105.0, lat - 35.0)
  let dLng = transformLon(lng - 105.0, lat - 35.0)
  const radLat = lat / 180.0 * PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / ((A * (1 - EE)) / (magic * sqrtMagic) * PI)
  dLng = (dLng * 180.0) / (A / sqrtMagic * Math.cos(radLat) * PI)
  return [lat + dLat, lng + dLng]
}
