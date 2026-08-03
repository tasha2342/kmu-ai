import { describeError } from '../lib/api.js'

/** 화면 어디서나 재사용하는 최소 단위 UI 조각들입니다. */

export function Badge({ children, className = '' }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium leading-none ${
        className || 'border-gray-200 bg-gray-100 text-gray-600'
      }`}
    >
      {children}
    </span>
  )
}

export function Button({ variant = 'primary', size = 'md', className = '', ...props }) {
  const variants = {
    primary: 'bg-kmu-800 text-white hover:bg-kmu-900 disabled:bg-kmu-300',
    secondary: 'border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:text-gray-400',
    subtle: 'bg-kmu-50 text-kmu-800 hover:bg-kmu-100 disabled:text-kmu-300',
    danger: 'border border-rose-200 bg-white text-rose-600 hover:bg-rose-50 disabled:text-rose-300',
    ghost: 'text-gray-500 hover:bg-gray-100 hover:text-gray-700',
  }
  const sizes = {
    sm: 'px-2.5 py-1 text-xs',
    md: 'px-3.5 py-2 text-sm',
    lg: 'px-5 py-2.5 text-sm',
  }
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed ${
        variants[variant]
      } ${sizes[size]} ${className}`}
    />
  )
}

export function Card({ title, description, actions, children, className = '', bodyClassName = '' }) {
  return (
    <section className={`rounded-xl border border-gray-200 bg-white ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3 sm:px-5">
          <div>
            {title && <h2 className="text-sm font-semibold text-gray-900">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={`px-4 py-4 sm:px-5 ${bodyClassName}`}>{children}</div>
    </section>
  )
}

export function Field({ label, hint, children, className = '' }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs font-medium text-gray-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-gray-400">{hint}</span>}
    </label>
  )
}

const controlClass =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:border-kmu-600 focus:outline-none focus:ring-1 focus:ring-kmu-600 disabled:bg-gray-50 disabled:text-gray-400'

export function Input({ className = '', ...props }) {
  return <input {...props} className={`${controlClass} ${className}`} />
}

export function Textarea({ className = '', ...props }) {
  return <textarea {...props} className={`${controlClass} ${className}`} />
}

export function Select({ className = '', children, ...props }) {
  return (
    <select {...props} className={`${controlClass} ${className}`}>
      {children}
    </select>
  )
}

/** 백엔드 오류(`{message, target}`)를 사용자에게 읽을 수 있게 보여줍니다. */
export function ErrorNotice({ error, onRetry, className = '' }) {
  if (!error) return null
  const isAuth = error.status === 401 || error.status === 403
  return (
    <div
      className={`rounded-lg border px-4 py-3 text-sm ${
        isAuth ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-rose-200 bg-rose-50 text-rose-800'
      } ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{describeError(error)}</p>
          {error.status > 0 && (
            <p className="mt-0.5 text-xs opacity-80">
              HTTP {error.status}
              {error.status === 503 && ' · 모델이 등록되지 않았거나 지식베이스 색인이 아직 준비되지 않았을 수 있습니다.'}
            </p>
          )}
          {error.status === 0 && (
            <p className="mt-0.5 text-xs opacity-80">kmu-ai-api 컨테이너가 기동 중인지 확인해주세요.</p>
          )}
        </div>
        {onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            다시 시도
          </Button>
        )}
      </div>
    </div>
  )
}

export function EmptyState({ title, description, action, className = '' }) {
  return (
    <div className={`rounded-lg border border-dashed border-gray-300 px-6 py-10 text-center ${className}`}>
      <p className="text-sm font-medium text-gray-600">{title}</p>
      {description && <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-gray-400">{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  )
}

export function Spinner({ className = '' }) {
  return (
    <span
      className={`inline-block animate-spin rounded-full border-2 border-current border-r-transparent ${className || 'h-4 w-4'}`}
      aria-hidden="true"
    />
  )
}

export function LoadingBlock({ label = '불러오는 중입니다…', className = '' }) {
  return (
    <div className={`flex items-center justify-center gap-2 py-10 text-sm text-gray-400 ${className}`}>
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

/** 표 형태 화면에서 로딩 중일 때 쓰는 스켈레톤 줄입니다. */
export function SkeletonRows({ rows = 5, className = '' }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="h-10 animate-pulse rounded-lg bg-gray-100" />
      ))}
    </div>
  )
}

export function Pagination({ page, totalPages, totalCount, onChange, className = '' }) {
  const pages = Math.max(1, totalPages || 1)
  if (pages <= 1 && !totalCount) return null

  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 pt-3 text-xs text-gray-500 ${className}`}>
      <span>총 {(totalCount ?? 0).toLocaleString('ko-KR')}건</span>
      <div className="flex items-center gap-1">
        <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          이전
        </Button>
        <span className="px-2 tabular-nums">
          {page} / {pages}
        </span>
        <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          다음
        </Button>
      </div>
    </div>
  )
}

/** 접근성을 위해 간단한 모달을 직접 구현합니다. (의존성 추가 없음) */
export function Modal({ open, title, description, onClose, children, footer, wide = false }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
      <div
        className={`flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl sm:rounded-2xl ${
          wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'
        }`}
        role="dialog"
        aria-modal="true"
      >
        <header className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
            {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="닫기">
            ✕
          </Button>
        </header>
        <div className="scrollbar-thin flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  )
}

/** 저장/삭제 결과 등 짧은 안내입니다. */
export function InlineNotice({ tone = 'info', children, className = '' }) {
  if (!children) return null
  const tones = {
    info: 'border-kmu-200 bg-kmu-50 text-kmu-800',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    warning: 'border-amber-200 bg-amber-50 text-amber-800',
    error: 'border-rose-200 bg-rose-50 text-rose-800',
  }
  return <div className={`rounded-lg border px-3 py-2 text-xs ${tones[tone]} ${className}`}>{children}</div>
}
