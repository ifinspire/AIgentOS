import { ReactNode } from 'react';
import { X } from 'lucide-react';

interface ChatOverlayCardProps {
  children: ReactNode;
  onDismiss?: () => void;
  capabilityName?: string;
}

export function ChatOverlayCard({ children, onDismiss, capabilityName }: ChatOverlayCardProps) {
  return (
    <div 
      className="rounded-lg p-4 mb-3 animate-in slide-in-from-bottom-2 duration-200"
      style={{
        backgroundColor: 'var(--aigent-color-surface-raised)',
        border: '1px solid var(--aigent-color-border)',
        boxShadow: 'var(--aigent-shadow-md)'
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          {capabilityName && (
            <div 
              className="text-xs uppercase tracking-wide mb-2"
              style={{ color: 'var(--aigent-color-text-muted)' }}
            >
              {capabilityName}
            </div>
          )}
          {children}
        </div>
        
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="flex-shrink-0 p-1 rounded hover:bg-opacity-10 hover:bg-black transition-colors"
            aria-label="Dismiss"
          >
            <X className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
          </button>
        )}
      </div>
    </div>
  );
}
