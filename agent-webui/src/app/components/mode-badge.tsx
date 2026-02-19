import { MessageSquare, Mic, Smartphone, LucideIcon } from 'lucide-react';

export type ModeType = 'chat' | 'voice' | 'mobile';

interface ModeBadgeProps {
  mode: ModeType;
  customLabel?: string;
  customIcon?: LucideIcon;
}

const modeConfig: Record<ModeType, { label: string; icon: LucideIcon; color: string }> = {
  chat: { label: 'Chat', icon: MessageSquare, color: 'oklch(0.488 0.243 264.376)' },
  voice: { label: 'Voice', icon: Mic, color: 'oklch(0.646 0.222 41.116)' },
  mobile: { label: 'Mobile', icon: Smartphone, color: 'oklch(0.6 0.118 184.704)' }
};

export function ModeBadge({ mode, customLabel, customIcon }: ModeBadgeProps) {
  const config = modeConfig[mode];
  const Icon = customIcon || config.icon;
  const label = customLabel || config.label;

  return (
    <div 
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm"
      style={{
        backgroundColor: `color-mix(in oklch, ${config.color} 15%, transparent)`,
        color: config.color,
        border: `1px solid color-mix(in oklch, ${config.color} 30%, transparent)`
      }}
    >
      <Icon className="w-3.5 h-3.5" />
      <span className="font-medium">{label}</span>
    </div>
  );
}
