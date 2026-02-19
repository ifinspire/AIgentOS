import { Calendar, Cloud, Users, Clock, Plus } from 'lucide-react';
import { SidebarWidget } from './sidebar-widget';
import { DashboardGridCard } from './dashboard-grid-card';

// Example Calendar Widget
export function CalendarWidget() {
  const events = [
    { time: '10:00 AM', title: 'Team Sync', color: 'oklch(0.488 0.243 264.376)' },
    { time: '2:00 PM', title: 'Project Review', color: 'oklch(0.646 0.222 41.116)' },
    { time: '4:30 PM', title: 'Design Discussion', color: 'oklch(0.6 0.118 184.704)' }
  ];

  return (
    <SidebarWidget 
      capabilityName="Calendar" 
      icon={Calendar}
      actions={
        <button 
          className="w-full py-2 px-3 rounded-md text-sm transition-colors"
          style={{
            backgroundColor: 'var(--aigent-color-primary)',
            color: '#ffffff'
          }}
        >
          <Plus className="w-4 h-4 inline mr-2" />
          Add Event
        </button>
      }
    >
      <div>
        <div className="text-sm mb-3" style={{ color: 'var(--aigent-color-text-muted)' }}>
          Today, Feb 18, 2026
        </div>
        <div className="space-y-2">
          {events.map((event, idx) => (
            <div 
              key={idx}
              className="flex gap-3 p-2 rounded-md"
              style={{ backgroundColor: 'var(--aigent-color-bg)' }}
            >
              <div 
                className="w-1 rounded-full"
                style={{ backgroundColor: event.color }}
              />
              <div className="flex-1 min-w-0">
                <div className="text-sm" style={{ color: 'var(--aigent-color-text)' }}>
                  {event.title}
                </div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--aigent-color-text-muted)' }}>
                  {event.time}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </SidebarWidget>
  );
}

// Example Weather Widget
export function WeatherWidget() {
  return (
    <SidebarWidget capabilityName="Weather" icon={Cloud}>
      <div className="text-center">
        <div className="text-4xl mb-2">☀️</div>
        <div className="text-2xl mb-1" style={{ color: 'var(--aigent-color-text)' }}>
          72°F
        </div>
        <div className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
          Sunny, light breeze
        </div>
        <div className="flex justify-around mt-4 pt-4" style={{ borderTop: '1px solid var(--aigent-color-border)' }}>
          <div className="text-center">
            <div className="text-xs mb-1" style={{ color: 'var(--aigent-color-text-muted)' }}>Thu</div>
            <div>🌤️</div>
            <div className="text-sm mt-1" style={{ color: 'var(--aigent-color-text)' }}>68°</div>
          </div>
          <div className="text-center">
            <div className="text-xs mb-1" style={{ color: 'var(--aigent-color-text-muted)' }}>Fri</div>
            <div>⛅</div>
            <div className="text-sm mt-1" style={{ color: 'var(--aigent-color-text)' }}>65°</div>
          </div>
          <div className="text-center">
            <div className="text-xs mb-1" style={{ color: 'var(--aigent-color-text-muted)' }}>Sat</div>
            <div>🌧️</div>
            <div className="text-sm mt-1" style={{ color: 'var(--aigent-color-text)' }}>61°</div>
          </div>
        </div>
      </div>
    </SidebarWidget>
  );
}

// Example Contacts Widget
export function ContactsWidget() {
  const contacts = [
    { name: 'Sarah Chen', status: 'online' },
    { name: 'Michael Rodriguez', status: 'away' },
    { name: 'Emma Watson', status: 'offline' }
  ];

  const statusColors = {
    online: '#22c55e',
    away: '#eab308',
    offline: '#64748b'
  };

  return (
    <SidebarWidget capabilityName="Contacts" icon={Users}>
      <div className="space-y-2">
        {contacts.map((contact, idx) => (
          <div 
            key={idx}
            className="flex items-center gap-3 p-2 rounded-md hover:bg-opacity-5 hover:bg-black cursor-pointer transition-colors"
          >
            <div className="relative">
              <div 
                className="w-8 h-8 rounded-full flex items-center justify-center"
                style={{ backgroundColor: 'var(--aigent-color-border)' }}
              >
                <span style={{ color: 'var(--aigent-color-text-muted)' }}>
                  {contact.name[0]}
                </span>
              </div>
              <div 
                className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full border-2"
                style={{ 
                  backgroundColor: statusColors[contact.status as keyof typeof statusColors],
                  borderColor: 'var(--aigent-color-surface)'
                }}
              />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate" style={{ color: 'var(--aigent-color-text)' }}>
                {contact.name}
              </div>
            </div>
          </div>
        ))}
      </div>
    </SidebarWidget>
  );
}

// Dashboard Card Examples
export function CalendarDashboardCard() {
  return (
    <DashboardGridCard capabilityName="Calendar" icon={Calendar}>
      <div>
        <div className="text-3xl mb-2" style={{ color: 'var(--aigent-color-text)' }}>3</div>
        <div className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
          events today
        </div>
        <div className="mt-4 text-sm" style={{ color: 'var(--aigent-color-text)' }}>
          Next: Team Sync at 10:00 AM
        </div>
      </div>
    </DashboardGridCard>
  );
}

export function WeatherDashboardCard() {
  return (
    <DashboardGridCard capabilityName="Weather" icon={Cloud}>
      <div className="flex items-center gap-4">
        <div className="text-5xl">☀️</div>
        <div>
          <div className="text-3xl mb-1" style={{ color: 'var(--aigent-color-text)' }}>72°F</div>
          <div className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
            Sunny, clear skies
          </div>
        </div>
      </div>
    </DashboardGridCard>
  );
}

export function TasksDashboardCard() {
  return (
    <DashboardGridCard capabilityName="Tasks" icon={Clock}>
      <div>
        <div className="flex items-baseline gap-2 mb-2">
          <span className="text-3xl" style={{ color: 'var(--aigent-color-text)' }}>5</span>
          <span className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>pending</span>
        </div>
        <div className="space-y-2 mt-4">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--aigent-color-status-active)' }} />
            <span className="text-sm" style={{ color: 'var(--aigent-color-text)' }}>
              Review design mockups
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--aigent-color-status-active)' }} />
            <span className="text-sm" style={{ color: 'var(--aigent-color-text)' }}>
              Update documentation
            </span>
          </div>
        </div>
      </div>
    </DashboardGridCard>
  );
}
