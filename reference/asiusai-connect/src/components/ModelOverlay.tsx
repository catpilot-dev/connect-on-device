import { RefObject, useEffect, useMemo, useRef } from 'react'
import type { HlsPlayerRef } from './HlsPlayer'
import type { FrameData } from '../log-reader/reader'
import { buildCarSpaceTransform, mapLineToPolygon, mapToScreen, findMaxIdx } from '../utils/projection'

interface ModelOverlayProps {
  playerRef: RefObject<HlsPlayerRef | null> | null  // null = live mode (no video sync)
  logFrames?: Record<string, FrameData>              // replay mode: indexed by frame ID
  liveFrame?: FrameData | null                       // live mode: single latest frame
  canvasWidth: number
  canvasHeight: number
  showPath: boolean
}

// --- Drawing helpers ---

function drawPoly(ctx: CanvasRenderingContext2D, points: [number, number][], color: string) {
  if (points.length < 3) return
  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1])
  ctx.closePath()
  ctx.fillStyle = color
  ctx.fill()
}

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

function accelToRgba(accel: number, alpha: number): string {
  const hue = Math.max(Math.min(60 + accel * 35, 120), 0)
  const sat = Math.max(Math.min(Math.abs(accel) * 1.5, 1.0), 0.5)
  const light = 0.82 - sat * 0.2
  const [r, g, b] = hslToRgb(hue, sat, light)
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
}

function drawPathWithAccel(ctx: CanvasRenderingContext2D, points: [number, number][], accelX: number[]) {
  if (points.length < 4) return
  let minY = Infinity, maxY = -Infinity
  for (const [, y] of points) {
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  if (maxY - minY < 1) return

  const nAccel = accelX.length
  const bands = Math.min(nAccel, 15)
  const bandStep = nAccel / bands
  const grad = ctx.createLinearGradient(0, maxY, 0, minY)
  let lastT = -1
  for (let b = 0; b < bands; b++) {
    const idx = Math.min(Math.floor(b * bandStep), nAccel - 1)
    const t = b / (bands - 1)
    if (t <= lastT) continue
    lastT = t
    let alpha: number
    if (t <= 0.5) alpha = 0.7
    else if (t >= 0.85) alpha = 0.0
    else alpha = 0.7 * (1 - (t - 0.5) / 0.35)
    grad.addColorStop(t, accelToRgba(accelX[idx], alpha))
  }

  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1])
  ctx.closePath()
  ctx.fillStyle = grad
  ctx.fill()
}

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

// --- HUD drawing functions ---

const FULL_W = 1928  // reference resolution

function drawEngagementBorder(ctx: CanvasRenderingContext2D, w: number, h: number, data: FrameData) {
  const sd = data.SelfdriveState
  const cs = data.CarState
  const borderW = 30 * (w / FULL_W)

  let color: string
  if (sd?.Enabled && (cs?.GearShifter === 0 || !cs)) {
    // Engaged
    color = 'rgba(23, 134, 68, 0.95)'
  } else if (sd?.Enabled) {
    // Override (engaged but gear not in park — could indicate driver override)
    color = 'rgba(23, 134, 68, 0.95)'
  } else {
    // Disengaged
    color = 'rgba(23, 51, 73, 0.8)'
  }

  // If gasPressed or brakePressed while engaged, show gray override
  if (sd?.Enabled && cs && (cs.AEgo < -2.0)) {
    color = 'rgba(145, 155, 149, 0.95)'
  }

  ctx.fillStyle = color
  // Top
  ctx.fillRect(0, 0, w, borderW)
  // Bottom
  ctx.fillRect(0, h - borderW, w, borderW)
  // Left
  ctx.fillRect(0, borderW, borderW, h - 2 * borderW)
  // Right
  ctx.fillRect(w - borderW, borderW, borderW, h - 2 * borderW)
}

function drawSpeedDisplay(ctx: CanvasRenderingContext2D, w: number, h: number, data: FrameData) {
  const cs = data.CarState
  if (!cs) return

  const scale = w / FULL_W
  const speedKmh = Math.round(cs.VEgo * 3.6)
  const speedStr = String(speedKmh)

  // Speed number
  const fontSize = Math.round(176 * scale)
  ctx.font = `bold ${fontSize}px Inter, system-ui, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = 'rgba(255, 255, 255, 0.95)'
  ctx.shadowColor = 'rgba(0, 0, 0, 0.5)'
  ctx.shadowBlur = 8 * scale
  const yPos = 50 * scale
  ctx.fillText(speedStr, w / 2, yPos)

  // Unit text
  const unitSize = Math.round(40 * scale)
  ctx.font = `${unitSize}px Inter, system-ui, sans-serif`
  ctx.fillStyle = 'rgba(255, 255, 255, 0.6)'
  ctx.fillText('km/h', w / 2, yPos + fontSize + 4 * scale)
  ctx.shadowBlur = 0
}

function drawMaxSpeedBox(ctx: CanvasRenderingContext2D, w: number, _h: number, data: FrameData) {
  const cs = data.CarState
  if (!cs) return

  const scale = w / FULL_W
  const boxW = 180 * scale
  const boxH = 130 * scale
  const boxX = 40 * scale
  const boxY = 40 * scale
  const radius = 16 * scale

  // Box background
  ctx.beginPath()
  ctx.roundRect(boxX, boxY, boxW, boxH, radius)
  ctx.fillStyle = 'rgba(0, 0, 0, 0.6)'
  ctx.fill()
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)'
  ctx.lineWidth = 2 * scale
  ctx.stroke()

  // "MAX" label
  const labelSize = Math.round(26 * scale)
  ctx.font = `bold ${labelSize}px Inter, system-ui, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = cs.CruiseEnabled ? 'rgba(23, 134, 68, 0.95)' : 'rgba(150, 150, 150, 0.8)'
  ctx.fillText('MAX', boxX + boxW / 2, boxY + 14 * scale)

  // Set speed number
  const cruiseKmh = Math.round(cs.CruiseSpeed * 3.6)
  if (cs.CruiseEnabled && cruiseKmh > 0) {
    const numSize = Math.round(56 * scale)
    ctx.font = `bold ${numSize}px Inter, system-ui, sans-serif`
    ctx.fillStyle = 'rgba(255, 255, 255, 0.95)'
    ctx.fillText(String(cruiseKmh), boxX + boxW / 2, boxY + 46 * scale)
  } else {
    const dashSize = Math.round(44 * scale)
    ctx.font = `${dashSize}px Inter, system-ui, sans-serif`
    ctx.fillStyle = 'rgba(150, 150, 150, 0.6)'
    ctx.fillText('--', boxX + boxW / 2, boxY + 52 * scale)
  }
}

function interpolateZ(posX: number[], posZ: number[], dist: number): number {
  // Find the path Z height at a given forward distance
  for (let i = 1; i < posX.length; i++) {
    if (posX[i] >= dist) {
      const t = (dist - posX[i - 1]) / (posX[i] - posX[i - 1])
      return posZ[i - 1] + t * (posZ[i] - posZ[i - 1])
    }
  }
  return posZ[posZ.length - 1] ?? 0
}

function drawLeadChevron(ctx: CanvasRenderingContext2D, w: number, _h: number, T: number[][], data: FrameData) {
  const lead = data.LeadOne
  if (!lead?.Status || lead.DRel <= 0) return
  if (!data.ModelV2?.Position) return

  const dRel = lead.DRel
  const yRel = lead.YRel
  const pathZ = interpolateZ(data.ModelV2.Position.X, data.ModelV2.Position.Z, dRel)

  const pt = mapToScreen(T, dRel, -yRel, pathZ + 1.2)
  if (!pt) return

  const [sx, sy] = pt
  const scale = w / FULL_W

  // Size scales inversely with distance (from openpilot)
  const sz = Math.max(15, Math.min(30, (25 * 30) / (dRel / 3 + 30))) * 2.35 * scale
  const halfW = sz * 0.7
  const chevH = sz * 0.5

  // Red fill alpha increases as distance decreases (within 40m)
  const fillAlpha = dRel < 40 ? Math.min(0.8, (40 - dRel) / 40 * 0.8) : 0

  // Gold glow
  ctx.save()
  ctx.shadowColor = 'rgba(218, 202, 37, 0.5)'
  ctx.shadowBlur = 16 * scale

  // Draw chevron (downward-pointing triangle)
  ctx.beginPath()
  ctx.moveTo(sx, sy)
  ctx.lineTo(sx - halfW, sy - chevH)
  ctx.lineTo(sx + halfW, sy - chevH)
  ctx.closePath()

  // Red fill (closer = more red)
  if (fillAlpha > 0.05) {
    ctx.fillStyle = `rgba(201, 34, 49, ${fillAlpha.toFixed(3)})`
    ctx.fill()
  }

  // Gold outline
  ctx.strokeStyle = 'rgba(218, 202, 37, 0.9)'
  ctx.lineWidth = 3 * scale
  ctx.stroke()
  ctx.restore()
}

function drawAlerts(ctx: CanvasRenderingContext2D, w: number, h: number, data: FrameData) {
  const sd = data.SelfdriveState
  if (!sd || sd.AlertSize === 0) return

  const scale = w / FULL_W
  const text1 = sd.AlertText1 || ''
  const text2 = sd.AlertText2 || ''
  if (!text1 && !text2) return

  // Background color based on alert status
  let bgColor: string
  switch (sd.AlertStatus) {
    case 1: bgColor = 'rgba(210, 120, 20, 0.85)'; break  // userPrompt: orange
    case 2: bgColor = 'rgba(201, 34, 49, 0.90)'; break   // critical: red
    default: bgColor = 'rgba(0, 0, 0, 0.75)'; break       // normal: black
  }

  // Measure text to size the box
  const text1Size = Math.round(sd.AlertSize >= 3 ? 72 * scale : 52 * scale)
  const text2Size = Math.round(sd.AlertSize >= 3 ? 44 * scale : 36 * scale)

  ctx.font = `bold ${text1Size}px Inter, system-ui, sans-serif`
  const m1 = ctx.measureText(text1)
  ctx.font = `${text2Size}px Inter, system-ui, sans-serif`
  const m2 = ctx.measureText(text2)

  const padX = 60 * scale
  const padY = 24 * scale
  const lineGap = text2 ? 12 * scale : 0
  const totalTextH = text1Size + (text2 ? lineGap + text2Size : 0)
  const boxW = Math.max(m1.width, m2.width) + padX * 2
  const boxH = totalTextH + padY * 2
  const boxX = (w - boxW) / 2
  const boxY = h - boxH - 50 * scale
  const radius = 16 * scale

  // Draw rounded background
  ctx.beginPath()
  ctx.roundRect(boxX, boxY, boxW, boxH, radius)
  ctx.fillStyle = bgColor
  ctx.fill()

  // Primary text
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.font = `bold ${text1Size}px Inter, system-ui, sans-serif`
  ctx.fillStyle = 'rgba(255, 255, 255, 0.95)'
  ctx.fillText(text1, w / 2, boxY + padY)

  // Secondary text
  if (text2) {
    ctx.font = `${text2Size}px Inter, system-ui, sans-serif`
    ctx.fillStyle = 'rgba(255, 255, 255, 0.8)'
    ctx.fillText(text2, w / 2, boxY + padY + text1Size + lineGap)
  }
}

function drawTurnSignals(ctx: CanvasRenderingContext2D, w: number, h: number, data: FrameData, blinkOn: boolean) {
  const cs = data.CarState
  if (!cs) return
  if (!cs.LeftBlinker && !cs.RightBlinker) return
  if (!blinkOn) return  // blink animation off-phase

  const scale = w / FULL_W
  const arrowW = 60 * scale
  const arrowH = 120 * scale
  const margin = 50 * scale
  const yCenter = h * 0.4

  ctx.fillStyle = 'rgba(30, 200, 60, 0.85)'

  // Left turn signal
  if (cs.LeftBlinker) {
    ctx.beginPath()
    ctx.moveTo(margin, yCenter)
    ctx.lineTo(margin + arrowW, yCenter - arrowH / 2)
    ctx.lineTo(margin + arrowW, yCenter + arrowH / 2)
    ctx.closePath()
    ctx.fill()
  }

  // Right turn signal
  if (cs.RightBlinker) {
    ctx.beginPath()
    ctx.moveTo(w - margin, yCenter)
    ctx.lineTo(w - margin - arrowW, yCenter - arrowH / 2)
    ctx.lineTo(w - margin - arrowW, yCenter + arrowH / 2)
    ctx.closePath()
    ctx.fill()
  }
}

// --- Main render ---

function renderFrame(
  ctx: CanvasRenderingContext2D,
  data: FrameData,
  T: number[][],
  canvasWidth: number,
  canvasHeight: number,
  showPath: boolean,
  blinkOn: boolean,
) {
  ctx.clearRect(0, 0, canvasWidth, canvasHeight)

  // 1. Engagement border (behind everything)
  if (data.SelfdriveState) {
    drawEngagementBorder(ctx, canvasWidth, canvasHeight, data)
  }

  if (data.ModelV2) {
    const maxDist = 100
    const maxIdx = findMaxIdx(data.ModelV2.Position.X, maxDist)

    // 2. Road edges (red, thin)
    if (data.ModelV2.RoadEdges) {
      for (const edge of data.ModelV2.RoadEdges) {
        const poly = mapLineToPolygon(T, edge, 0.025, 0, maxIdx)
        drawPoly(ctx, poly, 'rgba(255, 0, 0, 0.5)')
      }
    }

    // 3. Lane lines (white, opacity = probability)
    if (data.ModelV2.LaneLines) {
      for (const lane of data.ModelV2.LaneLines) {
        const prob = lane.prob ?? 0.5
        if (prob < 0.1) continue
        const poly = mapLineToPolygon(T, lane, 0.025 * prob, 0, maxIdx)
        drawPoly(ctx, poly, `rgba(255, 255, 255, ${Math.min(prob, 0.7)})`)
      }
    }

    // 4. Driving path
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

    // 5. Lead car chevron
    drawLeadChevron(ctx, canvasWidth, canvasHeight, T, data)
  }

  // 6. MAX speed box (top-left)
  drawMaxSpeedBox(ctx, canvasWidth, canvasHeight, data)

  // 7. Speed display (top-center)
  drawSpeedDisplay(ctx, canvasWidth, canvasHeight, data)

  // 8. Turn signals (left/right edges)
  drawTurnSignals(ctx, canvasWidth, canvasHeight, data, blinkOn)

  // 9. Alerts (bottom, on top of everything)
  drawAlerts(ctx, canvasWidth, canvasHeight, data)
}

// --- Component ---

export const ModelOverlay = ({ playerRef, logFrames = {}, liveFrame, canvasWidth, canvasHeight, showPath }: ModelOverlayProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const transformRef = useRef<number[][] | null>(null)
  const blinkRef = useRef(true)

  // Blink timer for turn signals (~2Hz toggle)
  useEffect(() => {
    const interval = setInterval(() => {
      blinkRef.current = !blinkRef.current
    }, 500)
    return () => clearInterval(interval)
  }, [])

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
    renderFrame(ctx, liveFrame, transformRef.current, canvasWidth, canvasHeight, showPath, blinkRef.current)
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
      if (data) renderFrame(ctx, data, transformRef.current!, canvasWidth, canvasHeight, showPath, blinkRef.current)
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
