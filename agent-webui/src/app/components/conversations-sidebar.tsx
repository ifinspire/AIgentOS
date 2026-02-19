import { Plus, MessageSquare, ChevronLeft, ChevronRight, Trash2 } from 'lucide-react';

interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  isActive?: boolean;
}

interface ConversationsSidebarProps {
  conversations: Conversation[];
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export function ConversationsSidebar({
  conversations,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
  isCollapsed,
  onToggleCollapse
}: ConversationsSidebarProps) {
  if (isCollapsed) {
    return (
      <div 
        className="w-12 flex flex-col items-center py-4 border-r"
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
        <button
          onClick={onNewChat}
          className="p-2 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="New chat"
        >
          <Plus className="w-5 h-5" style={{ color: 'var(--aigent-color-text)' }} />
        </button>
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
          Conversations
        </h3>
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
          aria-label="Collapse sidebar"
        >
          <ChevronLeft className="w-4 h-4" style={{ color: 'var(--aigent-color-text-muted)' }} />
        </button>
      </div>

      <div className="p-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-colors"
          style={{
            backgroundColor: 'var(--aigent-color-primary)',
            color: '#ffffff'
          }}
        >
          <Plus className="w-5 h-5" />
          <span className="font-medium">New Chat</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1">
        {conversations.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <MessageSquare 
              className="w-8 h-8 mx-auto mb-2 opacity-40" 
              style={{ color: 'var(--aigent-color-text-muted)' }} 
            />
            <p className="text-sm" style={{ color: 'var(--aigent-color-text-muted)' }}>
              No conversations yet
            </p>
          </div>
        ) : (
          conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => onSelectConversation(conv.id)}
              className="group w-full text-left px-4 py-3 rounded-lg transition-colors hover:bg-opacity-5 hover:bg-black"
              style={{
                backgroundColor: conv.isActive ? 'var(--aigent-color-bg)' : 'transparent',
                borderLeft: conv.isActive ? '3px solid var(--aigent-color-primary)' : '3px solid transparent'
              }}
            >
              <div className="flex items-start gap-2">
                <MessageSquare className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: 'var(--aigent-color-text-muted)' }} />
                <div className="flex-1 min-w-0">
                  <p className="font-medium mb-1 truncate" style={{ color: 'var(--aigent-color-text)' }}>
                    {conv.title}
                  </p>
                  <p className="text-sm truncate" style={{ color: 'var(--aigent-color-text-muted)' }}>
                    {conv.lastMessage}
                  </p>
                  <p className="text-xs mt-1" style={{ color: 'var(--aigent-color-text-muted)' }}>
                    {conv.timestamp}
                  </p>
                </div>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteConversation(conv.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      e.stopPropagation();
                      onDeleteConversation(conv.id);
                    }
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded transition-opacity"
                  style={{ color: 'var(--aigent-color-text-muted)' }}
                  aria-label={`Delete conversation ${conv.title}`}
                >
                  <Trash2 className="w-4 h-4" />
                </span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
