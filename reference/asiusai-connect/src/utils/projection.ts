import type { Pos } from '../log-reader/reader'

// Comma 3 road camera intrinsics (1928×1208)
const FCAM_INTRINSICS = [
  [2648, 0, 964],
  [0, 2648, 604],
  [0, 0, 1],
]

// device frame (X=fwd,Y=right,Z=down) → view frame (X=right,Y=down,Z=fwd)
const VIEW_FROM_DEVICE = [
  [0, 1, 0],
  [0, 0, 1],
  [1, 0, 0],
]

function matMul3(a: number[][], b: number[][]): number[][] {
  const r: number[][] = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ]
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      for (let k = 0; k < 3; k++) r[i][j] += a[i][k] * b[k][j]
  return r
}

function matVec3(m: number[][], v: number[]): number[] {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ]
}

// ZYX Euler: Rz(yaw) × Ry(pitch) × Rx(roll)
function euler2rot(rpy: number[]): number[][] {
  const [roll, pitch, yaw] = rpy
  const cr = Math.cos(roll),
    sr = Math.sin(roll)
  const cp = Math.cos(pitch),
    sp = Math.sin(pitch)
  const cy = Math.cos(yaw),
    sy = Math.sin(yaw)
  return [
    [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
    [-sp, cp * sr, cp * cr],
  ]
}

// Full camera resolution (Comma 3 road camera)
const FCAM_W = 1928
const FCAM_H = 1208

export function buildCarSpaceTransform(rpyCalib: number[], canvasW: number, canvasH: number): number[][] {
  const calibration = matMul3(VIEW_FROM_DEVICE, euler2rot(rpyCalib))
  const calibTransform = matMul3(FCAM_INTRINSICS, calibration)

  // Scale from camera pixel space (1928×1208) to canvas pixel space
  // qcamera is a proportional downscale of fcamera — same FOV, same aspect
  const videoTransform = [
    [canvasW / FCAM_W, 0, 0],
    [0, canvasH / FCAM_H, 0],
    [0, 0, 1],
  ]

  return matMul3(videoTransform, calibTransform)
}

function mapToScreen(T: number[][], x: number, y: number, z: number): [number, number] | null {
  const pt = matVec3(T, [x, y, z])
  if (pt[2] < 1.0) return null  // skip points < ~1m (avoids extreme perspective)
  return [pt[0] / pt[2], pt[1] / pt[2]]
}

export function findMaxIdx(xArr: number[], maxDist: number): number {
  for (let i = 0; i < xArr.length; i++) {
    if (xArr[i] > maxDist) return i
  }
  return xArr.length
}

export function mapLineToPolygon(
  T: number[][],
  line: Pos,
  yOff: number,
  zOff: number,
  maxIdx: number,
): [number, number][] {
  const left: [number, number][] = []
  const right: [number, number][] = []
  const n = Math.min(maxIdx, line.X.length)

  for (let i = 0; i < n; i++) {
    const x = line.X[i]
    const y = line.Y[i]
    const z = line.Z[i] + zOff

    const l = mapToScreen(T, x, y - yOff, z)
    const r = mapToScreen(T, x, y + yOff, z)
    if (l) left.push(l)
    if (r) right.push(r)
  }

  if (left.length < 2 || right.length < 2) return []
  return [...left, ...right.reverse()]
}
