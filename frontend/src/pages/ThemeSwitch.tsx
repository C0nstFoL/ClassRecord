import type { ThemeMode } from '../useTheme'

interface Props {
  mode: ThemeMode
  onChange: (mode: ThemeMode) => void
}

const OPTIONS: { value: ThemeMode; label: string; title: string }[] = [
  { value: 'system', label: '🖥️', title: '跟随系统' },
  { value: 'light', label: '☀️', title: '浅色模式' },
  { value: 'dark', label: '🌙', title: '深色模式' },
]

export default function ThemeSwitch({ mode, onChange }: Props) {
  return (
    <div className="theme-switch">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          title={opt.title}
          className={mode === opt.value ? 'active' : ''}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
