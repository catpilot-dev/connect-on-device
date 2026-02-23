/**
 * Human-readable names for 2°x2° OSM tile grid cells.
 * Key format: "lat,lon" (even integers, grid-aligned).
 * Each tile covers [lat, lat+2) x [lon, lon+2).
 */
const TILE_NAMES = {
  // Northeast
  '46,126': 'Harbin',
  '44,126': 'Changchun',
  '42,124': 'Shenyang',
  '40,122': 'Dalian',
  '38,120': 'Yantai / Weihai',

  // Beijing-Tianjin-Hebei
  '40,116': 'Beijing',
  '38,116': 'Tianjin / Tangshan',
  '38,114': 'Shijiazhuang',

  // Shandong
  '36,116': 'Jinan',
  '36,118': 'Qingdao / Weifang',
  '34,116': 'Xuzhou / Linyi',
  '34,118': 'Lianyungang',

  // Jiangsu / Anhui
  '32,118': 'Nanjing / Yangzhou',
  '32,120': 'Nantong / Yancheng',
  '32,116': 'Hefei / Chuzhou',

  // Shanghai / Zhejiang / Jiangsu
  '30,120': 'Shanghai / Hangzhou',
  '30,118': 'Huangshan / Jinhua',
  '30,116': 'Anqing / Jiujiang',
  '28,120': 'Taizhou / Wenling',
  '28,118': 'Wenzhou / Lishui',

  // Fujian
  '26,118': 'Fuzhou / Putian',
  '24,118': 'Xiamen / Zhangzhou',
  '26,116': 'Nanping / Sanming',
  '24,116': 'Longyan / Meizhou',

  // Jiangxi
  '28,114': 'Nanchang / Jiujiang',
  '28,116': 'Shangrao / Yingtan',
  '26,114': 'Ji\'an / Ganzhou',

  // Guangdong
  '22,112': 'Guangzhou / Shenzhen',
  '22,114': 'Shantou / Chaozhou',
  '22,110': 'Zhuhai / Zhongshan',
  '24,112': 'Shaoguan / Heyuan',

  // Hubei / Hunan
  '30,114': 'Wuhan',
  '30,112': 'Jingzhou / Yichang',
  '28,112': 'Changsha / Zhuzhou',
  '28,110': 'Hengyang / Yongzhou',
  '26,112': 'Chenzhou / Ganzhou',
  '26,110': 'Guilin / Yongzhou',

  // Guangxi
  '22,108': 'Nanning',
  '24,108': 'Liuzhou / Hechi',
  '24,110': 'Guilin / Hezhou',

  // Sichuan / Chongqing
  '30,104': 'Chengdu',
  '30,106': 'Chongqing',
  '28,104': 'Leshan / Yibin',
  '28,106': 'Zunyi / Luzhou',
  '32,104': 'Mianyang / Guangyuan',

  // Hainan
  '18,108': 'Haikou',
  '18,110': 'Sanya / Wanning',

  // Yunnan
  '24,102': 'Kunming',
  '22,100': 'Xishuangbanna',

  // Henan
  '34,112': 'Zhengzhou / Luoyang',
  '34,114': 'Kaifeng / Shangqiu',
  '32,112': 'Nanyang / Xinyang',

  // Shaanxi
  '34,108': 'Xi\'an',
  '32,108': 'Hanzhong',

  // Guizhou
  '26,106': 'Guiyang / Zunyi',
  '26,104': 'Kunming / Qujing',

  // Inner Mongolia
  '40,110': 'Hohhot / Baotou',
  '42,112': 'Ulanqab',

  // Xinjiang
  '44,86': 'Urumqi',
  '38,76': 'Kashgar',

  // Tibet
  '30,90': 'Lhasa',
}

/**
 * Get human-readable name for a tile, or null if not mapped.
 * @param {number} lat - Grid-aligned latitude (even integer)
 * @param {number} lon - Grid-aligned longitude (even integer)
 * @returns {string|null}
 */
export function tileName(lat, lon) {
  return TILE_NAMES[`${lat},${lon}`] || null
}

/**
 * Format tile label: "Shanghai / Hangzhou (30, 120)" or "30, 120"
 * @param {number} lat
 * @param {number} lon
 * @returns {string}
 */
export function tileLabel(lat, lon) {
  const name = tileName(lat, lon)
  return name ? `${name} (${lat}, ${lon})` : `${lat}, ${lon}`
}
