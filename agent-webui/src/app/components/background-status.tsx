import { ReactNode } from 'react';
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react';

export type StatusVariant = 'loading' | 'complete' | 'error';

interface BackgroundStatusProps {
  variant: StatusVariant;
  capabilityName: string;
  message: string;
  capabilityIcon?: ReactNode;
}

export function BackgroundStatus({ 
  variant, 
  capabilityName, 
  message,
  capabilityIcon 
}: BackgroundStatusProps) {
  const icons = {
    loading: <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--aigent-color-status-active)' }} />,
    complete: <CheckCircle2 className="w-4 h-4" style={{ color: 'var(--aigent-color-status-active)' }} />,
    error: <AlertCircle className="w-4 h-4 text-red-500" />
  };

  return (
    <div 
      className="flex items-center gap-3 px-4 py-3 mb-4 rounded-lg animate-in fade-in slide-in-from-top-2 duration-150"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        border: '1px solid var(--aigent-color-border)'
      }}
    >
      <div className="flex-shrink-0">
        {capabilityIcon || icons[variant]}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span 
            className="text-xs uppercase tracking-wide"
            style={{ color: 'var(--aigent-color-text-muted)' }}
          >
            {capabilityName}
          </span>
        </div>
        <p className="text-sm mt-0.5" style={{ color: 'var(--aigent-color-text)' }}>
          {message}
        </p>
      </div>
      <div className="flex-shrink-0">
        {icons[variant]}
      </div>
    </div>
  );
}
