import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import Hls from 'hls.js'

export interface HlsPlayerRef {
  seek(seconds: number): void
  play(): void
  pause(): void
  toggle(): void
  getCurrentTime(): number
  getDuration(): number
  isPlaying(): boolean
  isMuted(): boolean
  mute(): void
  unmute(): void
  setPlaybackRate(rate: number): void
  getVideoElement(): HTMLVideoElement | null
}

interface HlsPlayerProps {
  qcameraUrls: (string | undefined)[]
  className?: string
  autoPlay?: boolean
  initialTime?: number
  playbackRate?: number
}

export const HlsPlayer = forwardRef<HlsPlayerRef, HlsPlayerProps>(
  ({ qcameraUrls, className, autoPlay = true, initialTime, playbackRate = 1 }, ref) => {
    const videoRef = useRef<HTMLVideoElement>(null)
    const hlsRef = useRef<Hls | null>(null)

    useImperativeHandle(ref, () => ({
      seek(seconds: number) {
        if (videoRef.current) videoRef.current.currentTime = seconds
      },
      play() {
        videoRef.current?.play()
      },
      pause() {
        videoRef.current?.pause()
      },
      toggle() {
        const v = videoRef.current
        if (!v) return
        v.paused ? v.play() : v.pause()
      },
      getCurrentTime() {
        return videoRef.current?.currentTime ?? 0
      },
      getDuration() {
        return videoRef.current?.duration ?? 0
      },
      isPlaying() {
        return videoRef.current ? !videoRef.current.paused : false
      },
      isMuted() {
        return videoRef.current?.muted ?? true
      },
      mute() {
        if (videoRef.current) videoRef.current.muted = true
      },
      unmute() {
        if (videoRef.current) videoRef.current.muted = false
      },
      setPlaybackRate(rate: number) {
        if (videoRef.current) videoRef.current.playbackRate = rate
      },
      getVideoElement() {
        return videoRef.current
      },
    }))

    useEffect(() => {
      const video = videoRef.current
      if (!video || !qcameraUrls.length) return

      const data = [
        '#EXTM3U',
        '#EXT-X-VERSION:3',
        '#EXT-X-TARGETDURATION:61',
        '#EXT-X-MEDIA-SEQUENCE:0',
        '#EXT-X-PLAYLIST-TYPE:VOD',
        ...qcameraUrls.flatMap((file) =>
          !file ? ['#EXT-X-GAP', '#EXTINF:60.0,', 'gap'] : [`#EXTINF:60.0,`, file],
        ),
        '#EXT-X-ENDLIST',
      ].join('\n')

      const blob = new Blob([data], { type: 'application/vnd.apple.mpegurl' })
      const manifestUrl = URL.createObjectURL(blob)

      if (Hls.isSupported()) {
        const hls = new Hls({ enableWorker: true, lowLatencyMode: true })
        hls.loadSource(manifestUrl)
        hls.attachMedia(video)
        hlsRef.current = hls

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (initialTime) video.currentTime = initialTime
          if (autoPlay) video.play().catch(() => {})
        })
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = manifestUrl
        video.addEventListener(
          'loadedmetadata',
          () => {
            if (initialTime) video.currentTime = initialTime
            if (autoPlay) video.play().catch(() => {})
          },
          { once: true },
        )
      }

      return () => {
        hlsRef.current?.destroy()
        hlsRef.current = null
        URL.revokeObjectURL(manifestUrl)
      }
    }, [qcameraUrls])

    useEffect(() => {
      if (videoRef.current) videoRef.current.playbackRate = playbackRate
    }, [playbackRate])

    return <video ref={videoRef} className={className} muted playsInline style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
  },
)
