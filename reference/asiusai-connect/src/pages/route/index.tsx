import { RouteVideoPlayer, VideoControls } from '../../components/VideoPlayer'
import { useProfile, useRoute } from '../../api/queries'
import { useEffect, useRef, useState } from 'react'
import { HlsPlayerRef } from '../../components/HlsPlayer'
import { useRouteParams } from '../../utils/hooks'
import { TopAppBar } from '../../components/TopAppBar'
import { BackButton } from '../../components/BackButton'
import { callAthena } from '../../api/athena'
import { getStartEndPlaceName } from '../../utils/map'
import { DynamicMap } from './Map'
import { Stats } from './Stats'
import { Actions } from './Actions'
import { formatDate, formatTime } from '../../utils/format'
import { Info } from './Info'

const getLocationText = ({ start, end }: { start?: string; end?: string }) => {
  if (!start && !end) return 'Drive Details'
  if (!end || start === end) return `Drive in ${start}`
  if (!start) return `Drive in ${end}`
  return `${start} to ${end}`
}

export const Component = () => {
  const playerRef = useRef<HlsPlayerRef>(null)
  const { routeName, dongleId, date } = useRouteParams()

  const [route] = useRoute(routeName)
  const [profile] = useProfile()
  const [location, setLocation] = useState<{ start?: string; end?: string }>()

  useEffect(() => {
    if (route) getStartEndPlaceName(route).then(setLocation)
  }, [route])

  const isOwner = route && profile && route.user_id === profile.id
  useEffect(() => {
    if (isOwner) callAthena({ type: 'setRouteViewed', dongleId, params: { route: date } })
  }, [isOwner, date, dongleId])

  if (!route) return null

  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground">
      <TopAppBar leading={<BackButton href="/" />}>
        <span>{location ? getLocationText(location) : 'Drive details'}</span>
        {route.start_time && (
          <span className="text-xs md:text-sm font-medium text-white/60">
            {formatDate(route.start_time)} {formatTime(route.start_time)}
          </span>
        )}
      </TopAppBar>

      <div className="grid md:grid-cols-3 gap-3 md:gap-4 p-4 max-w-screen-xl mx-auto">
        <RouteVideoPlayer playerRef={playerRef} className="md:col-span-2 md:order-1" />
        <DynamicMap route={route} className="md:order-2" playerRef={playerRef} />
        <VideoControls playerRef={playerRef} className="md:col-span-2 md:order-3" />
        <Actions route={route} className="md:order-4" />
        <Stats route={route} className="md:col-span-2 md:order-5" />
        <Info route={route} className="md:order-6" />
      </div>
    </div>
  )
}
