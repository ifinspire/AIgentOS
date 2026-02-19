import { ReactNode } from 'react';
import { LucideIcon } from 'lucide-react';

interface DashboardGridCardProps {
  capabilityName: string;
  icon?: LucideIcon;
  customIcon?: ReactNode;
  children: ReactNode;
  isLoading?: boolean;
  onClick?: () => void;
}

export function DashboardGridCard({ 
  capabilityName, 
  icon: Icon, 
  customIcon,
  children, 
  isLoading,
  onClick 
}: DashboardGridCardProps) {
  return (
    <div 
      className={`rounded-lg overflow-hidden h-full flex flex-col ${onClick ? 'cursor-pointer hover:shadow-lg transition-shadow' : ''}`}
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        border: '1px solid var(--aigent-color-border)',
        boxShadow: 'var(--aigent-shadow-sm)'
      }}
      onClick={onClick}
    >
      {/* Header */}
      <div 
        className="px-5 py-4 flex items-center gap-3"
        style={{
          borderBottom: '1px solid var(--aigent-color-border)'
        }}
      >
        <div 
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{
            backgroundColor: 'var(--aigent-color-bg)'
          }}
        >
          {customIcon || (Icon && <Icon className="w-5 h-5" style={{ color: 'var(--aigent-color-primary)' }} />)}
        </div>
        <h3 className="font-medium" style={{ color: 'var(--aigent-color-text)' }}>
          {capabilityName}
        </h3>
      </div>

      {/* Content */}
      <div className="px-5 py-4 flex-1">
        {isLoading ? (
          <div className="space-y-3">
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)' }} />
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)', width: '90%' }} />
            <div className="h-4 rounded animate-pulse" style={{ backgroundColor: 'var(--aigent-color-border)', width: '70%' }} />
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

export function DashboardGridEmpty() {
  return (
    <div 
      className="col-span-full flex items-center justify-center py-24 text-center"
    >
      <div>
        <p className="text-lg" style={{ color: 'var(--aigent-color-text)' }}>
          No dashboard capabilities installed
        </p>
        <p className="text-sm mt-2" style={{ color: 'var(--aigent-color-text-muted)' }}>
          Capabilities with dashboard widgets will appear here
        </p>
      </div>
    </div>
  );
}
