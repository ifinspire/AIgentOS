import { Settings, LayoutDashboard, MessageSquare } from 'lucide-react';

interface HeaderProps {
  agentName: string;
  currentView: 'chat' | 'dashboard';
  onViewChange: (view: 'chat' | 'dashboard') => void;
  onSettingsClick?: () => void;
}

export function Header({ 
  agentName, 
  currentView,
  onViewChange,
  onSettingsClick
}: HeaderProps) {
  return (
    <header 
      className="flex items-center justify-between px-6 py-4"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        borderBottom: '1px solid var(--aigent-color-border)'
      }}
    >
      <div className="flex items-center gap-4">
        <h1 className="font-medium m-0" style={{ color: 'var(--aigent-color-text)' }}>
          {agentName}
        </h1>
      </div>

      <div className="flex items-center gap-2">
        <nav className="flex items-center gap-1 mr-4">
          <button
            onClick={() => onViewChange('chat')}
            className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors"
            style={{
              backgroundColor: currentView === 'chat' ? 'var(--aigent-color-primary)' : 'transparent',
              color: currentView === 'chat' ? '#ffffff' : 'var(--aigent-color-text)',
              border: currentView === 'chat' ? 'none' : '1px solid transparent'
            }}
          >
            <MessageSquare className="w-4 h-4" />
            Chat
          </button>
          <button
            onClick={() => onViewChange('dashboard')}
            className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors"
            style={{
              backgroundColor: currentView === 'dashboard' ? 'var(--aigent-color-primary)' : 'transparent',
              color: currentView === 'dashboard' ? '#ffffff' : 'var(--aigent-color-text)',
              border: currentView === 'dashboard' ? 'none' : '1px solid transparent'
            }}
          >
            <LayoutDashboard className="w-4 h-4" />
            Dashboard
          </button>
        </nav>
        
        <button
          onClick={onSettingsClick}
          className="p-2 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Settings"
        >
          <Settings className="w-5 h-5" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
      </div>
    </header>
  );
}
