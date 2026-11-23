import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumbs,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  breadcrumbs?: BreadcrumbItem[];
}) {
  return (
    <div className="mb-6 space-y-2">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1">
          <Link href="/dashboard" className="hover:text-foreground transition-colors">
            Dashboard
          </Link>
          {breadcrumbs.map((b, idx) => (
            <span key={idx} className="flex items-center gap-1.5">
              <ChevronRight className="h-3 w-3 text-muted-foreground/60" />
              {b.href ? (
                <Link href={b.href} className="hover:text-foreground transition-colors">
                  {b.label}
                </Link>
              ) : (
                <span className="text-foreground font-medium">{b.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
      </div>
    </div>
  );
}
