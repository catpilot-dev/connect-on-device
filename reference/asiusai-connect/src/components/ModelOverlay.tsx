import { RefObject, useEffect, useMemo, useRef } from 'react'
import type { HlsPlayerRef } from './HlsPlayer'
import type { FrameData } from '../log-reader/reader'
import { buildCarSpaceTransform, mapLineToPolygon, findMaxIdx } from '../utils/projection'

interface ModelOverlayProps {
  playerRef: RefObject<HlsPlayerRef | null> | null  // null = live mode (no video sync)
  logFrames?: Record<string, FrameData>              // replay mode: indexed by frame ID
  liveFrame?: FrameData | null                       // live mode: single latest frame
  canvasWidth: number
  canvasHeight: number
  showPath: boolean
}

function drawPoly(ctx: CanvasRenderingContext2D, points: [number, number][], color: string) {
  if (points.length < 3) return
  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1])
  ctx.closePath()
  ctx.fillStyle = color
  ctx.fill()
}

// HSL to RGB conversion (h: 0-360, s: 0-1, l: 0-1)
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  let r = 0, g = 0, b = 0
  if (h < 60) { r = c; g = x }
  else if (h < 120) { r = x; g = c }
  else if (h < 180) { g = c; b = x }
  else if (h < 240) { g = x; b = c }
  else if (h < 300) { r = x; b = c }
  else { r = c; b = x }
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)]
}

// Per-point color from openpilot (model_renderer.py:210-219)
// hue = clamp(60 + accel*35, 0, 120): red(braking) → yellow(coast) → green(accel)
function accelToRgba(accel: number, alpha: number): string {
  const hue = Math.max(Math.min(60 + accel * 35, 120), 0)
  const sat = Math.max(Math.min(Math.abs(accel) * 1.5, 1.0), 0.5)
  const light = 0.82 - sat * 0.2
  const [r, g, b] = hslToRgb(hue, sat, light)
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
}

// Draw path polygon with per-point acceleration coloring using vertical gradient
// Uses safe 2-stop gradient per color band to avoid issues with multi-stop ordering
function drawPathWithAccel(ctx: CanvasRenderingContext2D, points: [number, number][], accelX: number[]) {
  if (points.length < 4) return

  // Compute Y range across all polygon points
  let minY = Infinity, maxY = -Infinity
  for (const [, y] of points) {
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const range = maxY - minY
  if (range < 1) return

  // Sample ~15 bands along the acceleration array
  const nAccel = accelX.length
  const bands = Math.min(nAccel, 15)
  const bandStep = nAccel / bands

  // Build gradient with one stop per band
  const grad = ctx.createLinearGradient(0, maxY, 0, minY)
  let lastT = -1
  for (let b = 0; b < bands; b++) {
    const idx = Math.min(Math.floor(b * bandStep), nAccel - 1)
    const t = b / (bands - 1)  // 0=near, 1=far (evenly spaced)

    // Avoid duplicate stops
    if (t <= lastT) continue
    lastT = t

    // Alpha: solid near, fades far
    let alpha: number
    if (t <= 0.5) alpha = 0.7
    else if (t >= 0.85) alpha = 0.0
    else alpha = 0.7 * (1 - (t - 0.5) / 0.35)

    grad.addColorStop(t, accelToRgba(accelX[idx], alpha))
  }

  // Draw the polygon with gradient fill
  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1])
  ctx.closePath()
  ctx.fillStyle = grad
  ctx.fill()
}

// Fallback: single-color gradient when acceleration data is not available
function drawPathFallback(ctx: CanvasRenderingContext2D, points: [number, number][], aEgo: number) {
  if (points.length < 3) return

  let minY = Infinity, maxY = -Infinity
  for (const [, y] of points) {
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }

  const grad = ctx.createLinearGradient(0, maxY, 0, minY)
  grad.addColorStop(0, accelToRgba(aEgo, 0.7))
  grad.addColorStop(0.5, accelToRgba(aEgo, 0.7))
  grad.addColorStop(1, accelToRgba(aEgo, 0.0))

  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1])
  ctx.closePath()
  ctx.fillStyle = grad
  ctx.fill()
}

function renderFrame(ctx: CanvasRenderingContext2D, data: FrameData, T: number[][], canvasWidth: number, canvasHeight: number, showPath: boolean) {
  ctx.clearRect(0, 0, canvasWidth, canvasHeight)
  if (!data?.ModelV2) return

  const maxDist = 100
  const maxIdx = findMaxIdx(data.ModelV2.Position.X, maxDist)

  // Draw road edges (red, thin)
  if (data.ModelV2.RoadEdges) {
    for (const edge of data.ModelV2.RoadEdges) {
      const poly = mapLineToPolygon(T, edge, 0.025, 0, maxIdx)
      drawPoly(ctx, poly, 'rgba(255, 0, 0, 0.5)')
    }
  }

  // Draw lane lines (white, opacity = probability)
  if (data.ModelV2.LaneLines) {
    for (const lane of data.ModelV2.LaneLines) {
      const prob = lane.prob ?? 0.5
      if (prob < 0.1) continue
      const poly = mapLineToPolygon(T, lane, 0.025 * prob, 0, maxIdx)
      drawPoly(ctx, poly, `rgba(255, 255, 255, ${Math.min(prob, 0.7)})`)
    }
  }

  // Draw driving path with per-point acceleration coloring (matches openpilot experimental mode)
  if (showPath) {
    const pathPoly = mapLineToPolygon(T, data.ModelV2.Position, 0.9, 1.22, maxIdx)
    const accel = data.ModelV2.AccelerationX
    if (accel?.length) {
      try {
        drawPathWithAccel(ctx, pathPoly, accel)
      } catch {
        drawPathFallback(ctx, pathPoly, data.CarState?.AEgo ?? 0)
      }
    } else {
      drawPathFallback(ctx, pathPoly, data.CarState?.AEgo ?? 0)
    }
  }
}

export const ModelOverlay = ({ playerRef, logFrames = {}, liveFrame, canvasWidth, canvasHeight, showPath }: ModelOverlayProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const transformRef = useRef<number[][] | null>(null)

  // Sort frame IDs numerically (replay mode only)
  const sortedFrameIds = useMemo(() => {
    return Object.keys(logFrames)
      .map(Number)
      .sort((a, b) => a - b)
  }, [logFrames])

  // Build camera-space transform from calibration data
  useEffect(() => {
    if (canvasWidth === 0 || canvasHeight === 0) return

    const calib = liveFrame?.LiveCalibration
      ?? Object.values(logFrames).find((f) => f.LiveCalibration)?.LiveCalibration

    transformRef.current = buildCarSpaceTransform(
      calib?.RpyCalib ?? [0, 0, 0],
      canvasWidth,
      canvasHeight,
    )
  }, [liveFrame, logFrames, canvasWidth, canvasHeight])

  // Live mode: render on each frame update
  useEffect(() => {
    if (liveFrame === undefined) return  // Not in live mode
    const canvas = canvasRef.current
    if (!canvas || !liveFrame || !transformRef.current) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    renderFrame(ctx, liveFrame, transformRef.current, canvasWidth, canvasHeight, showPath)
  }, [liveFrame, canvasWidth, canvasHeight, showPath])

  // Replay mode: sync rendering to video time
  useEffect(() => {
    if (liveFrame !== undefined) return  // Skip in live mode
    const canvas = canvasRef.current
    if (!canvas || !transformRef.current || sortedFrameIds.length === 0) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let lastIdx = -1

    const render = () => {
      if (!transformRef.current) return
      const seconds = playerRef?.current?.getCurrentTime() ?? 0
      const segOffset = seconds % 60
      const idx = Math.min(Math.round(segOffset * 20), sortedFrameIds.length - 1)

      if (idx === lastIdx) return
      lastIdx = idx

      const frameId = sortedFrameIds[idx]
      const data = logFrames[String(frameId)]
      if (data) renderFrame(ctx, data, transformRef.current!, canvasWidth, canvasHeight, showPath)
    }

    // Sync overlay to video frames via requestVideoFrameCallback
    const video = playerRef?.current?.getVideoElement()
    let callbackId: number | undefined
    let fallbackId: ReturnType<typeof setInterval> | undefined

    if (video && 'requestVideoFrameCallback' in video) {
      const onFrame = () => {
        render()
        callbackId = (video as any).requestVideoFrameCallback(onFrame)
      }
      callbackId = (video as any).requestVideoFrameCallback(onFrame)
    } else {
      fallbackId = setInterval(render, 50)
    }

    render()
    return () => {
      if (callbackId !== undefined && video && 'cancelVideoFrameCallback' in video) {
        (video as any).cancelVideoFrameCallback(callbackId)
      }
      if (fallbackId !== undefined) clearInterval(fallbackId)
    }
  }, [logFrames, sortedFrameIds, playerRef, canvasWidth, canvasHeight, showPath, liveFrame])

  return (
    <canvas
      ref={canvasRef}
      width={canvasWidth}
      height={canvasHeight}
      className="absolute inset-0 w-full h-full pointer-events-none object-contain"
    />
  )
}
