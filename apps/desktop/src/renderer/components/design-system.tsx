import { useId, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

export type TypographyVariant = "display" | "page-title" | "section-title" | "body" | "muted" | "caption" | "label";
type TypographyTag = "h1" | "h2" | "h3" | "p" | "span" | "div";
const typographyTags: Record<TypographyVariant, TypographyTag> = {
  display: "h1",
  "page-title": "h1",
  "section-title": "h2",
  body: "p",
  muted: "p",
  caption: "span",
  label: "span",
};

export function Typography({ as, variant = "body", className = "", ...props }: HTMLAttributes<HTMLElement> & {
  as?: TypographyTag;
  variant?: TypographyVariant;
  className?: string;
}) {
  const Element = as ?? typographyTags[variant];
  return <Element className={`typography typography--${variant} ${className}`.trim()} {...props} />;
}

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button className={`button button--${variant} ${className}`.trim()} {...props} />;
}

export function Card({ className = "", ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={`panel ${className}`.trim()} {...props} />;
}

export function StatusIndicator({ tone, label, detail }: {
  tone: "success" | "warning" | "danger" | "neutral" | "info";
  label: string;
  detail?: string;
}) {
  return (
    <span role="status" aria-live="polite" className={`status-indicator status-indicator--${tone}`}>
      <span className="status-dot" aria-hidden="true" />
      <span>{label}</span>
      {detail && <small>{detail}</small>}
    </span>
  );
}

export function EmptyState({ title, description, icon, action }: {
  title: string;
  description: string;
  icon?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <span className="empty-state__mark" aria-hidden="true">{icon}</span>}
      <h3>{title}</h3>
      <span>{description}</span>
      {action}
    </div>
  );
}

export function DataNotice({ tone = "info", title, children }: {
  tone?: "info" | "warning";
  title: string;
  children: ReactNode;
}) {
  return (
    <div className={`data-notice data-notice--${tone}`}>
      <span className="data-notice__icon" aria-hidden="true">{tone === "warning" ? "!" : "i"}</span>
      <div><h3>{title}</h3><p>{children}</p></div>
    </div>
  );
}

export function InputField({ label, hint, className = "", ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; className?: string }) {
  const id = useId();
  return (
    <label className={`field ${className}`.trim()}>
      <span>{label}</span>
      <input id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props} />
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </label>
  );
}

export function SelectField({ label, hint, className = "", children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string; hint?: string; className?: string }) {
  const id = useId();
  return (
    <label className={`field ${className}`.trim()}>
      <span>{label}</span>
      <select id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props}>{children}</select>
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </label>
  );
}

export function DataTable({ caption, columns, rows, className = "" }: {
  caption: string;
  columns: string[];
  rows: ReactNode[][];
  className?: string;
}) {
  return (
    <div className="table-scroll">
      <table className={`data-table ${className}`.trim()}>
        <caption>{caption}</caption>
        <thead><tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => (
          <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function Toast({ tone = "info", children }: { tone?: "success" | "error" | "info"; children: ReactNode }) {
  return <div className={`toast toast--${tone}`} role={tone === "error" ? "alert" : "status"} aria-live={tone === "error" ? "assertive" : "polite"}>{children}</div>;
}

export function Modal({ open, title, onClose, children }: {
  open: boolean;
  title: string;
  onClose(): void;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="modal-backdrop">
      <section className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header><h2>{title}</h2><Button variant="quiet" aria-label="关闭对话框" onClick={onClose}>×</Button></header>
        <div>{children}</div>
      </section>
    </div>
  );
}

export function DetailPanel({ title, onClose, children }: {
  title: string;
  onClose(): void;
  children: ReactNode;
}) {
  return (
    <aside id="page-details" className="detail-panel" aria-label="页面详情">
      <header className="detail-panel__header">
        <div><Typography variant="label">当前上下文</Typography><Typography as="h2" variant="section-title">{title}</Typography></div>
        <Button variant="quiet" aria-label="关闭页面详情" onClick={onClose}>×</Button>
      </header>
      <div className="detail-panel__content">{children}</div>
    </aside>
  );
}
export function PageHeader({ eyebrow, title, description, action }: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<Typography variant="page-title">{title}</Typography><Typography variant="muted">{description}</Typography></div>
      {action && <div className="page-heading__action">{action}</div>}
    </div>
  );
}
