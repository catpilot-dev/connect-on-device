import clsx from 'clsx'
import { ButtonBase } from '../../components/ButtonBase'
import { Icon } from '../../components/Icon'
import { useStorage } from '../../utils/storage'
import { useState, useRef, useEffect } from 'react'

export const Navigation = ({ className }: { className?: string }) => {
  const [usingCorrectFork] = useStorage('usingCorrectFork')
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const items = [
    { title: 'Sentry', icon: 'photo_camera', href: '/sentry' },
    { title: 'Live', icon: 'play_arrow', href: '/live', hide: !usingCorrectFork },
    { title: 'Params', icon: 'switches', href: '/params', hide: !usingCorrectFork },
    { title: 'Analyze', icon: 'bar_chart', href: '/analyze' },
    { title: 'Settings', icon: 'settings', href: '/settings' },
  ]

  return (
    <div ref={ref} className={clsx('relative', className)}>
      <ButtonBase
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 transition-colors text-sm font-medium text-white/70"
      >
        <Icon name="menu" className="text-lg" />
        <span>Menu</span>
      </ButtonBase>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 bg-[#1e1e1e]/95 backdrop-blur-sm border border-white/10 rounded-xl shadow-2xl overflow-hidden min-w-[160px]">
          {items
            .filter((x) => !x.hide)
            .map(({ title, href, icon }) => (
              <ButtonBase
                key={title}
                href={href}
                className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/10 transition-colors text-sm text-white w-full"
              >
                <Icon name={icon as any} className="text-lg text-white/60" />
                <span>{title}</span>
              </ButtonBase>
            ))}
        </div>
      )}
    </div>
  )
}
