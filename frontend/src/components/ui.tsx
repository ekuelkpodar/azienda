import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiClientError } from "../api/client";

/* ---------------- primitives ---------------- */

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
type ButtonSize = "sm" | "md";

export function Button({
  variant = "secondary",
  size = "md",
  disabled,
  planned,
  plannedReason,
  onClick,
  type = "button",
  children,
  className = "",
  title,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  disabled?: boolean;
  /** Visible "Planned" label: button is disabled with a tooltip, per no-fake rule. */
  planned?: boolean;
  plannedReason?: string;
  onClick?: () => void;
  type?: "button" | "submit";
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const styles: Record<ButtonVariant, string> = {
    primary: "bg-neutral-900 text-white hover:bg-neutral-700 focus-visible:ring-neutral-900",
    secondary: "bg-white text-neutral-800 border border-neutral-300 hover:bg-neutral-50 focus-visible:ring-neutral-500",
    danger: "bg-red-700 text-white hover:bg-red-600 focus-visible:ring-red-700",
    ghost: "text-neutral-700 hover:bg-neutral-100 focus-visible:ring-neutral-500",
  };
  const sizes: Record<ButtonSize, string> = {
    sm: "px-2.5 py-1.5 text-xs",
    md: "px-4 py-2 text-sm",
  };
  const isDisabled = disabled || planned;
  return (
    <button
      type={type}
      disabled={isDisabled}
      onClick={onClick}
      title={planned ? (plannedReason ?? "Planned — not yet available in this build.") : title}
      aria-disabled={isDisabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${styles[variant]} ${sizes[size]} ${className}`}
    >
      {children}
      {planned && (
        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
          Planned
        </span>
      )}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-500 disabled:bg-neutral-100 ${props.className ?? ""}`}
    />
  );
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-500 ${props.className ?? ""}`}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-500 disabled:bg-neutral-100 ${props.className ?? ""}`}
    />
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-neutral-400">{hint}</span>}
    </label>
  );
}

/* ---------------- layout ---------------- */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-neutral-200 bg-white p-5 shadow-sm ${className}`}>
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-neutral-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "green" | "amber" | "red" | "blue" | "purple"; children: ReactNode }) {
  const tones: Record<string, string> = {
    neutral: "bg-neutral-100 text-neutral-700",
    green: "bg-green-100 text-green-800",
    amber: "bg-amber-100 text-amber-800",
    red: "bg-red-100 text-red-800",
    blue: "bg-blue-100 text-blue-800",
    purple: "bg-purple-100 text-purple-800",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function PlannedBadge({ reason }: { reason?: string }) {
  return (
    <span
      title={reason ?? "Planned — not yet available in this build."}
      className="inline-flex cursor-help items-center gap-1 rounded-full border border-dashed border-amber-400 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800"
    >
      Planned
    </span>
  );
}

/* ---------------- states ---------------- */

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-neutral-500" role="status" aria-live="polite">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-700" aria-hidden="true" />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-neutral-300 bg-white px-6 py-12 text-center">
      <p className="text-sm font-medium text-neutral-700">{title}</p>
      {hint && <p className="mx-auto mt-1 max-w-md text-xs text-neutral-500">{hint}</p>}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  context,
}: {
  error: ApiClientError | Error | null;
  onRetry?: () => void;
  context?: string;
}) {
  if (!error) return null;
  const isApi = error instanceof ApiClientError;
  const notImplemented = isApi && (error.status === 404 || error.status === 501 || error.code === "not_implemented");
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 px-6 py-8 text-center" role="alert">
      <p className="text-sm font-semibold text-red-800">
        {notImplemented ? "Not available yet" : "Something went wrong"}
      </p>
      <p className="mx-auto mt-1 max-w-lg text-xs text-red-700">
        {notImplemented
          ? `${context ?? "This endpoint"} is defined in the API contract but the backend has not implemented it yet. No data is shown rather than fake data.`
          : isApi
            ? `${error.body.message} (code: ${error.code})`
            : error.message}
      </p>
      {onRetry && (
        <div className="mt-4">
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}

/** Renders loading / error / content for a query. */
export function QueryView<T>({
  query,
  children,
  context,
}: {
  query: { data: T | null; error: ApiClientError | null; loading: boolean; reload: () => void };
  children: (data: T) => ReactNode;
  context?: string;
}) {
  if (query.loading) return <Spinner />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} context={context} />;
  if (query.data === null) return <Spinner />;
  return <>{children(query.data)}</>;
}

/* ---------------- table ---------------- */

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
}

export function DataTable<T extends { id: string | number }>({
  columns,
  rows,
  onRowClick,
  caption,
}: {
  columns: Column<T>[];
  rows: T[];
  onRowClick?: (row: T) => void;
  caption?: string;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white shadow-sm">
      <table className="w-full text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-neutral-500 ${c.className ?? ""}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`border-b border-neutral-100 last:border-0 ${onRowClick ? "cursor-pointer hover:bg-neutral-50 focus-within:bg-neutral-50" : ""}`}
            >
              {columns.map((c) => (
                <td key={c.key} className={`px-4 py-3 align-top text-neutral-800 ${c.className ?? ""}`}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- modal ---------------- */

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={`max-h-[90vh] w-full overflow-y-auto rounded-xl bg-white p-6 shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"}`}>
        <div className="mb-4 flex items-start justify-between gap-4">
          <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-md p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-500"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ---------------- tabs ---------------- */

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: T; label: string }[];
  active: T;
  onChange: (key: T) => void;
}) {
  return (
    <div role="tablist" aria-label="Sections" className="mb-4 flex gap-1 border-b border-neutral-200">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          onClick={() => onChange(t.key)}
          className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-500 ${
            active === t.key
              ? "border-neutral-900 text-neutral-900"
              : "border-transparent text-neutral-500 hover:text-neutral-800"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- toasts ---------------- */

interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
}

const ToastContext = createContext<{ push: (kind: Toast["kind"], message: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const push = useCallback((kind: Toast["kind"], message: string) => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, kind, message }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto rounded-lg border px-4 py-3 text-sm shadow-lg ${
              t.kind === "success"
                ? "border-green-200 bg-green-50 text-green-900"
                : t.kind === "error"
                  ? "border-red-200 bg-red-50 text-red-900"
                  : "border-neutral-200 bg-white text-neutral-900"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
