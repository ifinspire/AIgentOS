import { X, CheckCircle2, Clock3, Github, ExternalLink } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  modelName: string;
  backendEndpoint: string;
  healthEndpoint: string;
  backendConnected: boolean;
  backendReady: boolean;
  onDeleteAllData: () => void | Promise<void>;
  onExportData: () => void | Promise<void>;
}

export function SettingsModal({
  isOpen,
  onClose,
  modelName,
  backendEndpoint,
  healthEndpoint,
  backendConnected,
  backendReady,
  onDeleteAllData,
  onExportData,
}: SettingsModalProps) {
  if (!isOpen) return null;

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={onClose}
    >
      <div 
        className="w-full max-w-2xl rounded-xl overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        style={{
          backgroundColor: 'var(--aigent-color-surface)',
          border: '1px solid var(--aigent-color-border)'
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div 
          className="flex items-center justify-between px-6 py-4"
          style={{ borderBottom: '1px solid var(--aigent-color-border)' }}
        >
          <h2 className="m-0" style={{ color: 'var(--aigent-color-text)' }}>
            Settings
          </h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-opacity-5 hover:bg-black transition-colors"
          >
            <X className="w-5 h-5" style={{ color: 'var(--aigent-color-text-muted)' }} />
          </button>
        </div>

        {/* Content */}
        <div className="px-6 py-6 max-h-[70vh] overflow-y-auto">
          {/* Agent Info */}
          <section className="mb-8">
            <h3 className="mb-4" style={{ color: 'var(--aigent-color-text)' }}>
              Agent Configuration
            </h3>
            
            <div 
              className="p-4 rounded-lg mb-4"
              style={{
                backgroundColor: 'var(--aigent-color-bg)',
                border: '1px solid var(--aigent-color-border)'
              }}
            >
              <div className="flex justify-between items-start">
                <div>
                  <div className="text-sm mb-1" style={{ color: 'var(--aigent-color-text-muted)' }}>
                    Model
                  </div>
                  <div style={{ color: 'var(--aigent-color-text)' }}>
                    {modelName}
                  </div>
                </div>
              </div>
            </div>

            <p className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
              To modify agent configuration, edit your <code className="px-1.5 py-0.5 rounded" style={{ backgroundColor: 'var(--aigent-color-bg)', fontFamily: 'var(--aigent-font-mono)' }}>agent.yaml</code> file.
            </p>
          </section>

          {/* Ollama Connection */}
          <section className="mb-8">
            <h3 className="mb-4" style={{ color: 'var(--aigent-color-text)' }}>
              Backend Connection
            </h3>
            
            <div 
              className="p-4 rounded-lg"
              style={{
                backgroundColor: 'var(--aigent-color-bg)',
                border: '1px solid var(--aigent-color-border)'
              }}
            >
              <div className="flex items-center gap-3 mb-2">
                {backendConnected && backendReady ? (
                  <CheckCircle2 className="w-5 h-5 text-green-500" />
                ) : backendConnected ? (
                  <Clock3 className="w-5 h-5" style={{ color: 'var(--aigent-color-primary)' }} />
                ) : (
                  <Clock3 className="w-5 h-5" style={{ color: 'var(--aigent-color-rfi-accent)' }} />
                )}
                <span style={{ color: 'var(--aigent-color-text)' }}>
                  {backendConnected ? (backendReady ? 'Connected + Ready' : 'Connected (Warming)') : 'Disconnected'}
                </span>
              </div>
              <div className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
                Endpoint: {backendEndpoint}
              </div>
              <a
                href={healthEndpoint}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-sm mt-1"
                style={{ color: 'var(--aigent-color-primary)', textDecoration: 'none' }}
              >
                Health Check
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
              <div className="text-xs mt-1" style={{ color: 'var(--aigent-color-text-muted)' }}>
                {healthEndpoint}
              </div>
            </div>
          </section>

          {/* About */}
          <section className="mb-8">
            <h3 className="mb-4" style={{ color: 'var(--aigent-color-text)' }}>
              About
            </h3>
            
            <div 
              className="p-4 rounded-lg space-y-4"
              style={{
                backgroundColor: 'var(--aigent-color-bg)',
                border: '1px solid var(--aigent-color-border)'
              }}
            >
              <div>
                <div className="text-sm mb-1" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  Version
                </div>
                <div className="font-medium" style={{ color: 'var(--aigent-color-text)' }}>
                  v0.1.0-alpha
                </div>
              </div>

              <div>
                <div className="text-sm mb-2" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  AIgentOS is an open-source, self-hosted private AI operating system.
                </div>
                <a 
                  href="https://github.com/ifinspire/AIgentOS" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg transition-colors hover:bg-opacity-10 hover:bg-black"
                  style={{
                    backgroundColor: 'var(--aigent-color-surface)',
                    color: 'var(--aigent-color-text)',
                    border: '1px solid var(--aigent-color-border)',
                    textDecoration: 'none'
                  }}
                >
                  <Github className="w-4 h-4" />
                  <span>View on GitHub</span>
                  <ExternalLink className="w-3.5 h-3.5" style={{ color: 'var(--aigent-color-text-muted)' }} />
                </a>
              </div>

              <div className="pt-3 border-t" style={{ borderColor: 'var(--aigent-color-border)' }}>
                <div className="text-xs" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  Licensed under MIT • Built with privacy in mind
                </div>
              </div>
            </div>
          </section>

          {/* Danger Zone */}
          <section>
            <h3 className="mb-4" style={{ color: 'var(--aigent-color-rfi-accent)' }}>
              Data Controls
            </h3>
            <div
              className="p-4 rounded-lg"
              style={{
                backgroundColor: 'var(--aigent-color-bg)',
                border: '1px solid var(--aigent-color-border)'
              }}
            >
              <div className="flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm mb-1" style={{ color: 'var(--aigent-color-text)' }}>
                    Export Backup
                  </p>
                  <p className="text-xs" style={{ color: 'var(--aigent-color-text-muted)' }}>
                    Download conversations, prompts, and perf telemetry as JSON.
                  </p>
                </div>
                <button
                  onClick={() => void onExportData()}
                  className="px-4 py-2 rounded-lg text-sm font-medium"
                  style={{
                    backgroundColor: 'var(--aigent-color-surface)',
                    color: 'var(--aigent-color-text)',
                    border: '1px solid var(--aigent-color-border)'
                  }}
                >
                  Export Backup
                </button>
              </div>
              <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--aigent-color-border)' }}>
                <p className="text-sm mb-3" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  Permanently deletes all local conversations, performance history, and prompt customizations.
                </p>
                <button
                  onClick={() => void onDeleteAllData()}
                  className="px-4 py-2 rounded-lg text-sm font-medium"
                  style={{
                    backgroundColor: 'var(--aigent-color-rfi-accent)',
                    color: '#fff'
                  }}
                >
                  Delete All Data
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
