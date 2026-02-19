import { ReactNode } from 'react';
import { Bot, User, Info } from 'lucide-react';

export type MessageBubbleVariant = 'user' | 'agent' | 'system' | 'rfi';

interface MessageBubbleProps {
  variant: MessageBubbleVariant;
  children: ReactNode;
  timestamp?: string;
  reasoning?: string;
  capabilityName?: string;
  capabilityIcon?: ReactNode;
}

export function MessageBubble({ 
  variant, 
  children, 
  timestamp,
  reasoning,
  capabilityName,
  capabilityIcon
}: MessageBubbleProps) {
  const isUser = variant === 'user';
  const isRFI = variant === 'rfi';
  const isSystem = variant === 'system';

  return (
    <div 
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'} mb-4`}
      style={{
        animation: 'fadeIn var(--aigent-transition-fast) ease-out'
      }}
    >
      {/* Avatar */}
      {!isSystem && (
        <div 
          className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center"
          style={{
            backgroundColor: isUser 
              ? 'var(--aigent-color-primary)' 
              : 'var(--aigent-color-surface)',
            border: isUser ? 'none' : '1px solid var(--aigent-color-border)'
          }}
        >
          {isUser ? (
            <User className="w-4 h-4 text-white" />
          ) : (
            <Bot className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
          )}
        </div>
      )}

      {/* Message Content */}
      <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} max-w-[70%]`}>
        {/* RFI Attribution */}
        {isRFI && capabilityName && (
          <div className="flex items-center gap-1.5 mb-1.5 px-3">
            {capabilityIcon}
            <span 
              className="text-xs"
              style={{ color: 'var(--aigent-color-text-muted)' }}
            >
              Asked by {capabilityName}
            </span>
          </div>
        )}

        <div 
          className="px-4 py-3 rounded-2xl"
          style={{
            backgroundColor: isUser 
              ? 'var(--aigent-color-primary)' 
              : isSystem 
              ? 'transparent'
              : 'var(--aigent-color-surface)',
            color: isUser ? '#ffffff' : 'var(--aigent-color-text)',
            border: isUser || isSystem ? 'none' : '1px solid var(--aigent-color-border)',
            borderLeft: isRFI ? '3px solid var(--aigent-color-rfi-accent)' : undefined,
            boxShadow: isSystem ? 'none' : 'var(--aigent-shadow-sm)'
          }}
        >
          {isSystem && (
            <div className="flex items-center gap-2 mb-2">
              <Info className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
              <span className="text-xs uppercase tracking-wide" style={{ color: 'var(--aigent-color-text-muted)' }}>
                System
              </span>
            </div>
          )}
          <div className="whitespace-pre-wrap break-words">{children}</div>
          {!isUser && !isSystem && reasoning && (
            <details className="mt-3">
              <summary
                className="cursor-pointer text-xs"
                style={{ color: 'var(--aigent-color-text-muted)' }}
              >
                Reasoning
              </summary>
              <div
                className="mt-2 p-3 rounded text-xs whitespace-pre-wrap break-words"
                style={{
                  backgroundColor: 'var(--aigent-color-bg)',
                  border: '1px solid var(--aigent-color-border)',
                  color: 'var(--aigent-color-text-muted)'
                }}
              >
                {reasoning}
              </div>
            </details>
          )}
        </div>

        {timestamp && (
          <span 
            className="text-xs mt-1 px-3"
            style={{ color: 'var(--aigent-color-text-muted)' }}
          >
            {timestamp}
          </span>
        )}
      </div>
    </div>
  );
}

// Add keyframe animation
const style = document.createElement('style');
style.textContent = `
  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
`;
document.head.appendChild(style);
