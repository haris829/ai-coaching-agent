/** Small shared UI primitives: badges, modal, toasts, spinner, error summary. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { ApiError } from '../api/client';
import {
  QUESTION_TYPE_LABELS,
  type QuestionStatus,
  type QuestionType,
} from '../api/types';

// ---------------------------------------------------------------------------
// Badges
// ---------------------------------------------------------------------------

export function StatusBadge({ status }: { status: QuestionStatus }): ReactNode {
  const className =
    status === 'ACTIVE' ? 'badge-active' : status === 'RETIRED' ? 'badge-retired' : 'badge-draft';
  const title =
    status === 'RETIRED'
      ? 'Withheld from future quizzes; still fully available for historical reporting'
      : status === 'DRAFT'
        ? 'Not yet publishable — never delivered'
        : 'Eligible for future quiz delivery';
  return (
    <span className={`badge ${className}`} title={title}>
      {status}
    </span>
  );
}

export function TypeBadge({ type }: { type: QuestionType }): ReactNode {
  return (
    <span className="badge badge-type" title={type}>
      {QUESTION_TYPE_LABELS[type]}
    </span>
  );
}

export function Spinner({ label }: { label?: string }): ReactNode {
  return (
    <span className="row tight">
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="muted">{label}</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Error summary
// ---------------------------------------------------------------------------

/**
 * Renders a backend failure, including the field-level detail produced by the authoritative
 * validator. Showing the server's own messages — rather than a generic "save failed" — is what
 * makes the strict backend validation usable.
 */
export function ErrorSummary({ error }: { error: unknown }): ReactNode {
  if (!error) return null;

  if (error instanceof ApiError) {
    return (
      <div className="alert alert-error" role="alert">
        <strong>{error.message}</strong>
        {error.details.length > 0 ? (
          <ul>
            {error.details.map((issue, index) => (
              <li key={`${issue.field}-${issue.code}-${index}`}>
                <code>{issue.field}</code> — {issue.message}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="subtle" style={{ marginTop: 6 }}>
          {error.code} · HTTP {error.status}
        </div>
      </div>
    );
  }

  return (
    <div className="alert alert-error" role="alert">
      <strong>Something went wrong</strong>
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

export function Modal({
  title,
  children,
  footer,
  onClose,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
}): ReactNode {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal">
        <div className="modal-header">
          <h2>{title}</h2>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

type ToastKind = 'success' | 'error' | 'info';
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  success(message: string): void;
  error(message: string): void;
  info(message: string): void;
}

const ToastContext = createContext<ToastApi>({
  success: () => {},
  error: () => {},
  info: () => {},
});

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

let toastId = 0;

export function ToastProvider({ children }: { children: ReactNode }): ReactNode {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((kind: ToastKind, message: string) => {
    toastId += 1;
    const id = toastId;
    setToasts((current) => [...current, { id, kind, message }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, kind === 'error' ? 7000 : 4000);
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      success: (message) => push('success', message),
      error: (message) => push('error', message),
      info: (message) => push('info', message),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`}>
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function truncate(value: string, max = 130): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
