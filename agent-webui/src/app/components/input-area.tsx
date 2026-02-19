import { useState, KeyboardEvent, useRef, useEffect } from 'react';
import { Send, Loader2 } from 'lucide-react';

interface InputAreaProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  isProcessing?: boolean;
  placeholder?: string;
  resetSignal?: number;
  validateMessage?: (message: string) => string | null;
  getUsage?: (message: string) => {
    estimatedTokens: number;
    maxContext: number;
    pct: number;
    exceeds: boolean;
    includesCompactInstructions: boolean;
  };
}

export function InputArea({ 
  onSend, 
  disabled, 
  isProcessing,
  placeholder = "Message your agent...",
  resetSignal = 0,
  validateMessage,
  getUsage,
}: InputAreaProps) {
  const [message, setMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [usage, setUsage] = useState<{
    estimatedTokens: number;
    maxContext: number;
    pct: number;
    exceeds: boolean;
    includesCompactInstructions: boolean;
  } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [message]);

  useEffect(() => {
    if (!validateMessage) {
      setErrorMessage(null);
      return;
    }
    setErrorMessage(validateMessage(message));
  }, [message, validateMessage]);

  useEffect(() => {
    if (!getUsage) {
      setUsage(null);
      return;
    }
    setUsage(getUsage(message));
  }, [message, getUsage]);

  useEffect(() => {
    if (isProcessing || disabled) return;
    textareaRef.current?.focus();
  }, [isProcessing, disabled]);

  useEffect(() => {
    setMessage('');
    setErrorMessage(null);
    if (getUsage) {
      setUsage(getUsage(''));
    } else {
      setUsage(null);
    }
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.focus();
    }
  }, [resetSignal, getUsage]);

  const handleSend = () => {
    const validationError = validateMessage ? validateMessage(message) : null;
    if (validationError) {
      setErrorMessage(validationError);
      return;
    }
    if (message.trim() && !disabled && !isProcessing) {
      onSend(message.trim());
      setMessage('');
      setErrorMessage(null);
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div 
      className="px-6 py-4"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        borderTop: '1px solid var(--aigent-color-border)'
      }}
    >
      <div 
        className="flex items-end gap-3 p-3 rounded-lg"
        style={{
          backgroundColor: 'var(--aigent-color-bg)',
          border: '1px solid var(--aigent-color-border)'
        }}
      >
        <textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || isProcessing}
          placeholder={placeholder}
          className="flex-1 bg-transparent border-none outline-none resize-none min-h-[24px] max-h-[220px] overflow-y-auto"
          style={{
            color: 'var(--aigent-color-text)',
            fontFamily: 'var(--aigent-font-sans)'
          }}
          rows={1}
        />
        
        <button
          onClick={handleSend}
          disabled={!message.trim() || disabled || isProcessing || Boolean(errorMessage)}
          className="flex-shrink-0 p-2 rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          style={{
            backgroundColor: 'var(--aigent-color-primary)',
            color: '#ffffff'
          }}
          aria-label="Send message"
        >
          {isProcessing ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </button>
      </div>
      {usage && (
        <p className="mt-2 text-xs" style={{ color: usage.exceeds ? 'var(--aigent-color-rfi-accent)' : 'var(--aigent-color-text-muted)' }}>
          Prompt est: {usage.estimatedTokens} / {usage.maxContext} ({usage.pct}%){usage.includesCompactInstructions ? " · compact-instructions included" : ""}
        </p>
      )}
      {errorMessage && (
        <p className="mt-2 text-sm" style={{ color: 'var(--aigent-color-rfi-accent)' }}>
          {errorMessage}
        </p>
      )}
    </div>
  );
}
