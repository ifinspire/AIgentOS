import { Activity, Cpu, Database, ChevronLeft, ChevronRight, FileText, Bug, FlaskConical } from 'lucide-react';

export type DashboardSection = 'prompts' | 'performance' | 'baseline' | 'debug' | 'database' | 'logs';

interface DashboardSidebarProps {
  activeSection: DashboardSection;
  onSectionChange: (section: DashboardSection) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

const sections = [
  { id: 'prompts' as DashboardSection, label: 'Prompts', icon: FileText },
  { id: 'performance' as DashboardSection, label: 'Performance', icon: Activity },
  { id: 'baseline' as DashboardSection, label: 'Baseline', icon: FlaskConical },
  { id: 'debug' as DashboardSection, label: 'Debug', icon: Bug },
  { id: 'database' as DashboardSection, label: 'Database', icon: Database },
  { id: 'logs' as DashboardSection, label: 'Logs', icon: Cpu }
];

export function DashboardSidebar({
  activeSection,
  onSectionChange,
  isCollapsed,
  onToggleCollapse
}: DashboardSidebarProps) {
  if (isCollapsed) {
    return (
      <div 
        className="w-16 flex flex-col items-center py-4 border-r"
        style={{
          backgroundColor: 'var(--aigent-color-surface)',
          borderColor: 'var(--aigent-color-border)'
        }}
      >
        <button
          onClick={onToggleCollapse}
          className="p-2 rounded-lg mb-4 transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Expand sidebar"
        >
          <ChevronRight className="w-5 h-5" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
        <div className="space-y-2">
          {sections.map(({ id, icon: Icon }) => (
            <button
              key={id}
              onClick={() => onSectionChange(id)}
              className="p-2.5 rounded-lg transition-colors"
              style={{
                backgroundColor: activeSection === id ? 'var(--aigent-color-primary)' : 'transparent',
                color: activeSection === id ? '#ffffff' : 'var(--aigent-color-text-muted)'
              }}
              aria-label={id}
            >
              <Icon className="w-5 h-5" />
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div 
      className="w-64 flex flex-col border-r"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        borderColor: 'var(--aigent-color-border)'
      }}
    >
      <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: 'var(--aigent-color-border)' }}>
        <h3 className="font-medium m-0" style={{ color: 'var(--aigent-color-text)' }}>
          Dashboard
        </h3>
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Collapse sidebar"
        >
          <ChevronLeft className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-1">
        {sections.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onSectionChange(id)}
            className="w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
            style={{
              backgroundColor: activeSection === id ? 'var(--aigent-color-primary)' : 'transparent',
              color: activeSection === id ? '#ffffff' : 'var(--aigent-color-text)',
              borderLeft: activeSection === id ? '3px solid var(--aigent-color-primary)' : '3px solid transparent'
            }}
          >
            <Icon className="w-5 h-5" />
            <span className="font-medium">{label}</span>
          </button>
        ))}
      </div>

      <div 
        className="p-4 border-t"
        style={{ borderColor: 'var(--aigent-color-border)' }}
      >
        <div className="text-xs space-y-1" style={{ color: 'var(--aigent-color-text-muted)' }}>
          <div className="flex justify-between">
            <span>System Status</span>
            <span className="font-medium" style={{ color: 'var(--aigent-color-status-active)' }}>Online</span>
          </div>
          <div className="flex justify-between">
            <span>Version</span>
            <span className="font-medium" style={{ color: 'var(--aigent-color-text)' }}>v0.1.0</span>
          </div>
        </div>
      </div>
    </div>
  );
}
