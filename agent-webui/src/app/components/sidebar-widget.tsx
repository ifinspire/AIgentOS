import { ReactNode } from 'react';
import { LucideIcon } from 'lucide-react';

interface SidebarWidgetProps {
  capabilityName: string;
  icon?: LucideIcon;
  customIcon?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  isLoading?: boolean;
}

export function SidebarWidget({ 
  capabilityName, 
  icon: Icon, 
  customIcon,
  children, 
  actions,
  isLoading 
}: SidebarWidgetProps) {
  return (
    <div 
      className="rounded-lg overflow-hidden"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        border: '1px solid var(--aigent-color-border)',
        boxShadow: 'var(--aigent-shadow-sm)'
      }}
    >
      {/* Header */}
      <div 
        className="px-4 py-3 flex items-center gap-2"
        style={{
          borderBottom: '1px solid var(--aigent-color-border)'
        }}
      >
        {customIcon || (Icon && <Icon className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />)}
        <span className="font-medium" style={{ color: 'var(--aigent-color-text)' }}>
          {capabilityName}
        </span>
      </div>

      {/* Content */}
      <div className="px-4 py-3">
        {isLoading ? (
          <div className="space-y-2">
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)' }} />
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)', width: '80%' }} />
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)', width: '60%' }} />
          </div>
        ) : (
          children
        )}
      </div>

      {/* Actions Footer */}
      {actions && (
        <div 
          className="px-4 py-3"
          style={{
            borderTop: '1px solid var(--aigent-color-border)',
            backgroundColor: 'var(--aigent-color-bg)'
          }}
        >
          {actions}
        </div>
      )}
    </div>
  );
}

export function SidebarWidgetEmpty() {
  return (
    <div 
      className="h-full flex items-center justify-center px-6 py-12 text-center"
    >
      <div>
        <p style={{ color: 'var(--aigent-color-text-muted)' }}>
          No active capabilities
        </p>
        <p className="text-xs mt-2" style={{ color: 'var(--aigent-color-text-muted)' }}>
          Capabilities will appear here when activated
        </p>
      </div>
    </div>
  );
}
