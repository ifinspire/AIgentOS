import { useEffect, useRef } from 'react';
import { ChevronLeft, ChevronRight, Calendar, Cloud, CheckCircle2, AlertCircle, Clock } from 'lucide-react';

interface CapabilityUpdate {
  id: string;
  capabilityName: string;
  status: 'success' | 'error' | 'processing';
  message: string;
  timestamp: string;
  icon?: React.ReactNode;
}

interface CapabilityUpdatesPanelProps {
  updates: CapabilityUpdate[];
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

const statusIcons = {
  success: <CheckCircle2 className="w-4 h-4" style={{ color: 'var(--aigent-color-status-active)' }} />,
  error: <AlertCircle className="w-4 h-4" style={{ color: 'var(--aigent-color-rfi-accent)' }} />,
  processing: <Clock className="w-4 h-4" style={{ color: 'var(--aigent-color-primary)' }} />
};

export function CapabilityUpdatesPanel({
  updates,
  isCollapsed,
  onToggleCollapse
}: CapabilityUpdatesPanelProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (isCollapsed || !listRef.current) return;
    listRef.current.scrollTop = 0;
  }, [updates.length, isCollapsed]);

  if (isCollapsed) {
    return (
      <div 
        className="w-12 flex flex-col items-center py-4 border-l"
        style={{
          backgroundColor: 'var(--aigent-color-surface)',
          borderColor: 'var(--aigent-color-border)'
        }}
      >
        <button
          onClick={onToggleCollapse}
          className="p-2 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Expand updates panel"
        >
          <ChevronLeft className="w-5 h-5" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
        {updates.length > 0 && (
          <div 
            className="mt-4 w-2 h-2 rounded-full"
            style={{ backgroundColor: 'var(--aigent-color-status-active)' }}
          />
        )}
      </div>
    );
  }

  return (
    <div 
      className="w-80 flex flex-col border-l"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        borderColor: 'var(--aigent-color-border)'
      }}
    >
      <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: 'var(--aigent-color-border)' }}>
        <h3 className="font-medium m-0" style={{ color: 'var(--aigent-color-text)' }}>
          Agent Updates
        </h3>
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Collapse updates panel"
        >
          <ChevronRight className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
      </div>

      <div ref={listRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {updates.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <Clock 
              className="w-8 h-8 mx-auto mb-2 opacity-40" 
              style={{ color: 'var(--aigent-color-text-muted)' }} 
            />
            <p className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
              No recent updates
            </p>
          </div>
        ) : (
          updates.map((update) => (
            <div
              key={update.id}
              className="p-4 rounded-lg"
              style={{
                backgroundColor: 'var(--aigent-color-bg)',
                border: '1px solid var(--aigent-color-border)'
              }}
            >
              <div className="flex items-start gap-3 mb-2">
                {update.icon || statusIcons[update.status]}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm" style={{ color: 'var(--aigent-color-text)' }}>
                      {update.capabilityName}
                    </span>
                  </div>
                  <p className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
                    {update.message}
                  </p>
                </div>
              </div>
              <div className="flex items-center justify-between mt-2">
                <span className="text-xs" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  {update.timestamp}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
