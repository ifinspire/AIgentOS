import { useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import { Activity, Bot, Brain, Cpu, Database, MessageSquare, Trash2, Zap } from "lucide-react";

import { Header } from "./components/header";
import { Footer } from "./components/footer";
import { InputArea } from "./components/input-area";
import { MessageBubble } from "./components/message-bubble";
import { SettingsModal } from "./components/settings-modal";
import { ConversationsSidebar } from "./components/conversations-sidebar";
import { CapabilityUpdatesPanel } from "./components/capability-updates-panel";
import { DashboardSidebar, DashboardSection } from "./components/dashboard-sidebar";
import {
  api,
  ApiBaselineJobStatusResponse,
  ApiBaselineRunResponse,
  ApiContextSettings,
  ApiConversationSummary,
  ApiDebugLog,
  ApiInteractionEvent,
  ApiMemoryChunk,
  ApiMessage,
  ApiPerformanceExchange,
  ApiPerformanceMetrics,
  ApiPerformanceSummary,
  ApiPromptComponent,
  ApiSystemPrompt,
  API_BASE_URL,
} from "./lib/api";

interface Message {
  id: string;
  variant: "user" | "agent" | "system" | "rfi";
  content: string;
  timestamp: string;
  reasoning?: string;
  capabilityName?: string;
}

interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  isActive?: boolean;
}

interface CapabilityUpdate {
  id: string;
  capabilityName: string;
  status: "success" | "error" | "processing";
  message: string;
  timestamp: string;
  icon?: React.ReactNode;
}

interface PerfExchange {
  id: string;
  conversationId: string;
  timestamp: string;
  userPreview: string;
  assistantPreview: string;
  perf: ApiPerformanceMetrics;
}

interface MemoryChunkView extends ApiMemoryChunk {}

const EMPTY_PERF: ApiPerformanceMetrics = {
  total_latency_ms: 0,
  llm_latency_ms: 0,
  ttft_ms: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  retrieved_chunk_count: 0,
  retrieved_chunks: [],
  prompt_breakdown: {
    system_chars: 0,
    user_chars: 0,
    assistant_chars: 0,
    system_tokens_est: null,
    user_tokens_est: null,
    assistant_tokens_est: null,
  },
};

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRelative(ts: string) {
  const d = new Date(ts);
  const deltaMin = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (deltaMin < 1) return "Just now";
  if (deltaMin === 1) return "1 min ago";
  if (deltaMin < 60) return `${deltaMin} min ago`;
  return formatTime(ts);
}

function formatMs(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatTokensPerSecond(completionTokens: number | null | undefined, latencyMs: number | null | undefined) {
  if (completionTokens == null || latencyMs == null || latencyMs <= 0) return "-";
  const tps = completionTokens / (latencyMs / 1000);
  if (!Number.isFinite(tps) || tps <= 0) return "-";
  return `${tps.toFixed(1)} tok/s`;
}

function computeTokensPerSecond(completionTokens: number | null | undefined, latencyMs: number | null | undefined) {
  if (completionTokens == null || latencyMs == null || latencyMs <= 0) return null;
  const tps = completionTokens / (latencyMs / 1000);
  if (!Number.isFinite(tps) || tps <= 0) return null;
  return tps;
}

function summarizeBaseline(result: ApiBaselineRunResponse) {
  const ttftValues: number[] = [];
  const throughputValues: number[] = [];

  for (const category of result.categories) {
    for (const baselineCase of category.cases) {
      if (baselineCase.per_turn_latency_ms && baselineCase.per_turn_latency_ms.length > 0) {
        for (let idx = 0; idx < baselineCase.per_turn_latency_ms.length; idx += 1) {
          const ttft = baselineCase.per_turn_ttft_ms?.[idx];
          if (typeof ttft === "number" && ttft > 0) {
            ttftValues.push(ttft);
          }
          const completionTokens = baselineCase.per_turn_completion_tokens?.[idx] ?? null;
          const latencyMs = baselineCase.per_turn_latency_ms[idx];
          const throughput = computeTokensPerSecond(completionTokens, latencyMs);
          if (throughput != null) {
            throughputValues.push(throughput);
          }
        }
        continue;
      }

      if (typeof baselineCase.ttft_ms === "number" && baselineCase.ttft_ms > 0) {
        ttftValues.push(baselineCase.ttft_ms);
      }
      const throughput = computeTokensPerSecond(
        baselineCase.completion_tokens,
        baselineCase.completion_time_ms ?? baselineCase.total_latency_ms,
      );
      if (throughput != null) {
        throughputValues.push(throughput);
      }
    }
  }

  const avgTtftMs =
    ttftValues.length > 0
      ? Math.round(ttftValues.reduce((sum, value) => sum + value, 0) / ttftValues.length)
      : null;
  const avgThroughput =
    throughputValues.length > 0
      ? throughputValues.reduce((sum, value) => sum + value, 0) / throughputValues.length
      : null;

  return {
    avgTtftMs,
    avgThroughput,
    minTtftMs: ttftValues.length > 0 ? Math.min(...ttftValues) : null,
    maxTtftMs: ttftValues.length > 0 ? Math.max(...ttftValues) : null,
    minThroughput: throughputValues.length > 0 ? Math.min(...throughputValues) : null,
    maxThroughput: throughputValues.length > 0 ? Math.max(...throughputValues) : null,
  };
}

function ellipsize(text: string, max = 80) {
  const trimmed = (text || "").trim();
  if (!trimmed) return "...";
  if (trimmed.length <= max) return `${trimmed}...`;
  return `${trimmed.slice(0, max)}...`;
}

function parseAssistantContent(text: string): { visible: string; reasoning?: string } {
  const raw = text || "";
  const thinkRegex = /<think>([\s\S]*?)<\/think>/gi;
  const reasoningParts: string[] = [];
  let match: RegExpExecArray | null;

  while (true) {
    match = thinkRegex.exec(raw);
    if (!match) break;
    const captured = (match[1] || "").trim();
    if (captured) reasoningParts.push(captured);
  }

  const visible = raw
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/<\/?think>/gi, "")
    .trim();
  const reasoning = reasoningParts.join("\n\n").trim();

  if (!visible && reasoning) {
    return {
      visible: "(No final response text)",
      reasoning,
    };
  }
  return {
    visible: visible || raw.trim(),
    reasoning: reasoning || undefined,
  };
}

function estimateTokens(text: string) {
  return Math.max(1, Math.ceil((text || "").length / 4));
}

function mapApiMessage(message: ApiMessage): Message {
  const parsed =
    message.role === "assistant"
      ? parseAssistantContent(message.content)
      : { visible: message.content, reasoning: undefined };
  return {
    id: message.id,
    variant: message.role === "user" ? "user" : "agent",
    content: parsed.visible,
    reasoning: parsed.reasoning,
    timestamp: formatTime(message.timestamp),
  };
}

function mapApiEvent(event: ApiInteractionEvent): Message | null {
  if (event.role !== "user" && event.role !== "assistant") return null;
  const parsed =
    event.role === "assistant"
      ? parseAssistantContent(event.content)
      : { visible: event.content, reasoning: undefined };
  return {
    id: event.id,
    variant: event.role === "user" ? "user" : "agent",
    content: parsed.visible,
    reasoning: parsed.reasoning,
    timestamp: formatTime(event.timestamp),
  };
}

function mapConversationEvents(events: ApiInteractionEvent[]): Message[] {
  return events
    .map(mapApiEvent)
    .filter((item): item is Message => item !== null);
}

function buildCapabilityMessage(events: ApiInteractionEvent[], perf?: ApiPerformanceExchange | null) {
  const userEvents = events.filter((event) => event.role === "user");
  const assistantEvents = events.filter((event) => event.role === "assistant");
  const latestUser = userEvents[userEvents.length - 1];
  const latestAssistant = assistantEvents[assistantEvents.length - 1];

  if (latestUser?.status === "failed") {
    return { status: "error" as const, message: latestUser.error || "Message processing failed" };
  }
  if (latestAssistant?.status === "failed") {
    return { status: "error" as const, message: latestAssistant.error || "Assistant response failed" };
  }
  if (latestAssistant?.status === "completed") {
    const compaction = perf?.metrics.context_compaction;
    if (perf) {
      if (compaction?.applied) {
        return {
          status: "success" as const,
          message: `Done in ${formatMs(perf.metrics.total_latency_ms)} · Context compacted (${compaction.dropped_history_messages} old messages dropped)`,
        };
      }
      return {
        status: "success" as const,
        message: `Done in ${formatMs(perf.metrics.total_latency_ms)}`,
      };
    }
    return { status: "success" as const, message: "Response complete" };
  }
  if (latestAssistant?.status === "processing") {
    return { status: "processing" as const, message: "Streaming response..." };
  }
  if (latestUser?.status === "processing") {
    return { status: "processing" as const, message: "Thinking..." };
  }
  return { status: "processing" as const, message: "Queued for worker..." };
}

function mapConversation(item: ApiConversationSummary, activeId: string | null): Conversation {
  return {
    id: item.id,
    title: item.title || "New Conversation",
    lastMessage: item.last_message || "No messages yet",
    timestamp: formatRelative(item.updated_at),
    isActive: item.id === activeId,
  };
}

function mapPerfExchange(exchange: ApiPerformanceExchange): PerfExchange {
  return {
    id: exchange.id,
    conversationId: exchange.conversation_id,
    timestamp: exchange.created_at,
    userPreview: exchange.user_preview,
    assistantPreview: exchange.assistant_preview,
    perf: exchange.metrics,
  };
}

export default function App() {
  const [currentView, setCurrentView] = useState<"chat" | "dashboard">("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [capabilityUpdates, setCapabilityUpdates] = useState<CapabilityUpdate[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [latestPerf, setLatestPerf] = useState<ApiPerformanceMetrics>(EMPTY_PERF);
  const [perfHistory, setPerfHistory] = useState<PerfExchange[]>([]);
  const [runtimeModel, setRuntimeModel] = useState("unknown");
  const [backendConnected, setBackendConnected] = useState(false);
  const [apiEndpoint, setApiEndpoint] = useState(API_BASE_URL);
  const [isModelWarm, setIsModelWarm] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState<ApiSystemPrompt | null>(null);
  const [promptComponents, setPromptComponents] = useState<ApiPromptComponent[]>([]);
  const [promptContentDrafts, setPromptContentDrafts] = useState<Record<string, string>>({});
  const [promptEnabledDrafts, setPromptEnabledDrafts] = useState<Record<string, boolean>>({});
  const [performanceSummary, setPerformanceSummary] = useState<ApiPerformanceSummary | null>(null);
  const [contextSettings, setContextSettings] = useState<ApiContextSettings | null>(null);
  const [contextSettingsDraft, setContextSettingsDraft] = useState<{
    max_response_tokens: string;
    compact_trigger_pct: string;
    compact_instructions: string;
    memory_enabled: boolean;
  }>({
    max_response_tokens: "1024",
    compact_trigger_pct: "0.9",
    compact_instructions: "",
    memory_enabled: true,
  });
  const [debugLogs, setDebugLogs] = useState<ApiDebugLog[]>([]);
  const [memoryChunks, setMemoryChunks] = useState<MemoryChunkView[]>([]);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [expandedDebugIds, setExpandedDebugIds] = useState<Record<string, boolean>>({});
  const [inputResetSignal, setInputResetSignal] = useState(0);
  const [baselineRunning, setBaselineRunning] = useState(false);
  const [baselineResult, setBaselineResult] = useState<ApiBaselineRunResponse | null>(null);
  const [baselineStatus, setBaselineStatus] = useState<ApiBaselineJobStatusResponse | null>(null);
  const [baselineJobId, setBaselineJobId] = useState<string | null>(null);
  const [baselineEnforceMaxResponseTokens, setBaselineEnforceMaxResponseTokens] = useState(true);
  const [baselineMode, setBaselineMode] = useState<"direct_model" | "end_to_end_aigentos">("direct_model");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const streamRef = useRef<EventSource | null>(null);

  const [conversationsSidebarCollapsed, setConversationsSidebarCollapsed] = useState(false);
  const [updatesPanelCollapsed, setUpdatesPanelCollapsed] = useState(false);
  const [dashboardSidebarCollapsed, setDashboardSidebarCollapsed] = useState(false);
  const [dashboardSection, setDashboardSection] = useState<DashboardSection>("prompts");

  const refreshConversations = async (preferActiveId?: string | null) => {
    const list = await api.listConversations();
    const nextActiveId = preferActiveId !== undefined ? preferActiveId : activeConversationId;
    setConversations(list.map((item) => mapConversation(item, nextActiveId ?? null)));
    return list;
  };

  const loadConversation = async (conversationId: string) => {
    const detail = await api.getConversationEvents(conversationId);
    setActiveConversationId(conversationId);
    setMessages(mapConversationEvents(detail.events));
    await refreshConversations(conversationId);
  };

  const loadDashboardData = async () => {
    const [promptData, componentsData, summaryData, contextData, debugData, memoryData] = await Promise.all([
      api.getSystemPrompt(),
      api.getPromptComponents(),
      api.getPerformanceSummary(),
      api.getContextSettings(),
      api.getDebugLogs(50),
      api.getMemoryChunks(200),
    ]);
    setSystemPrompt(promptData);
    setPromptComponents(componentsData);
    setPerformanceSummary(summaryData);
    setContextSettings(contextData);
    setContextSettingsDraft({
      max_response_tokens: String(contextData.max_response_tokens),
      compact_trigger_pct: String(contextData.compact_trigger_pct),
      compact_instructions: contextData.compact_instructions || "",
      memory_enabled: contextData.memory_enabled,
    });
    setMemoryEnabled(memoryData.memory_enabled);
    setMemoryChunks(memoryData.chunks);
    setPromptContentDrafts(Object.fromEntries(componentsData.map((c) => [c.id, c.content])));
    setPromptEnabledDrafts(Object.fromEntries(componentsData.map((c) => [c.id, c.enabled])));
    setDebugLogs(debugData);
  };

  useEffect(() => {
    const initialize = async () => {
      try {
        const health = await api.healthCheck();
        setRuntimeModel(health.model);
        setApiEndpoint(API_BASE_URL);
        setBackendConnected(true);
        setIsModelWarm(health.is_warm);
        setCapabilityUpdates([
          {
            id: "kernel-online",
            capabilityName: "Kernel",
            status: "success",
            message: `Connected (${health.model})`,
            timestamp: "Just now",
            icon: <Cpu className="w-4 h-4" style={{ color: "var(--aigent-color-status-active)" }} />,
          },
          {
            id: "ollama-warmup",
            capabilityName: "Ollama",
            status: "processing",
            message: "Warming up model...",
            timestamp: "Just now",
            icon: <Bot className="w-4 h-4" style={{ color: "var(--aigent-color-primary)" }} />,
          },
        ]);

        const list = await api.listConversations();
        const recentPerf = await api.getRecentPerformance(5);
        await loadDashboardData();
        try {
          const warmup = await api.warmupLLM();
          setCapabilityUpdates((prev) =>
            prev.map((u) =>
              u.id === "ollama-warmup"
                ? {
                    ...u,
                    status: "success",
                    message:
                      warmup.status === "already_warmed"
                        ? `Model Ready (${warmup.model})`
                        : `Model Ready in ${formatMs(warmup.latency_ms)} (${warmup.model})`,
                    timestamp: "Just now",
                  }
                : u,
            ),
          );
          setIsModelWarm(true);
        } catch (error) {
          const msg = error instanceof Error ? error.message : "Warmup failed";
          setCapabilityUpdates((prev) =>
            prev.map((u) =>
              u.id === "ollama-warmup"
                ? { ...u, status: "error", message: `Warmup failed: ${msg}`, timestamp: "Just now" }
                : u,
            ),
          );
          setIsModelWarm(false);
        }
        setPerfHistory(recentPerf.map(mapPerfExchange));
        if (recentPerf.length > 0) {
          setLatestPerf(recentPerf[0].metrics);
        }
        if (list.length === 0) {
          setConversations([]);
          setMessages([]);
          setActiveConversationId(null);
          return;
        }

        const first = list[0];
        setConversations(list.map((item) => mapConversation(item, first.id)));
        await loadConversation(first.id);
      } catch (error) {
        const msg = error instanceof Error ? error.message : "Failed to connect to backend";
        setBackendConnected(false);
        setIsModelWarm(false);
        toast.error(msg);
        setMessages([
          {
            id: "boot-error",
            variant: "system",
            content: `Kernel API is unavailable: ${msg}`,
            timestamp: formatTime(new Date().toISOString()),
          },
        ]);
      }
    };
    void initialize();
  }, []);

  useEffect(() => {
    if (currentView !== "dashboard") return;
    if (dashboardSection === "prompts" || dashboardSection === "debug" || dashboardSection === "memory") {
      void loadDashboardData();
    }
  }, [currentView, dashboardSection]);

  useEffect(() => {
    if (currentView !== "chat") return;
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isProcessing, currentView]);

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    if (!activeConversationId) return;

    const source = new EventSource(api.conversationStreamUrl(activeConversationId));
    streamRef.current = source;

    source.addEventListener("conversation", (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      try {
        const detail = JSON.parse(event.data) as {
          events: ApiInteractionEvent[];
          latest_performance?: ApiPerformanceExchange | null;
          performance_summary?: ApiPerformanceSummary | null;
        };
        setMessages(mapConversationEvents(detail.events));
        if (detail.latest_performance) {
          setLatestPerf(detail.latest_performance.metrics);
          setPerfHistory((prev) => {
            const withoutCurrent = prev.filter((item) => item.id !== detail.latest_performance?.id);
            return [mapPerfExchange(detail.latest_performance), ...withoutCurrent].slice(0, 5);
          });
        }
        if (detail.performance_summary) {
          setPerformanceSummary(detail.performance_summary);
        }
        setCapabilityUpdates((prev) => {
          const next = [...prev];
          const latest = next[0];
          const state = buildCapabilityMessage(detail.events, detail.latest_performance ?? null);
          const payload = {
            id: latest?.id ?? `stream-${activeConversationId}`,
            capabilityName: "LLM",
            status: state.status,
            message: state.message,
            timestamp: "Just now",
            icon: <Bot className="w-4 h-4" style={{ color: "var(--aigent-color-primary)" }} />,
          };
          if (latest && latest.capabilityName === "LLM") {
            next[0] = { ...latest, ...payload };
            return next;
          }
          return [payload, ...next].slice(0, 20);
        });
      } catch {
        // Ignore malformed SSE payloads.
      }
    });

    source.onerror = () => {
      // Browser auto-reconnect is enough for this lightweight baseline.
    };

    return () => {
      source.close();
      if (streamRef.current === source) {
        streamRef.current = null;
      }
    };
  }, [activeConversationId]);

  const handleSendMessage = async (content: string) => {
    if (isProcessing) return;
    setIsProcessing(true);

    let conversationId = activeConversationId;

    const pendingUpdateId = `${Date.now()}-pending`;
    setCapabilityUpdates((prev) => [
      {
        id: pendingUpdateId,
        capabilityName: "LLM",
        status: "processing",
        message: "Generating response...",
        timestamp: "Just now",
        icon: <Bot className="w-4 h-4" style={{ color: "var(--aigent-color-primary)" }} />,
      },
      ...prev,
    ]);

    const optimisticUserMessage: Message = {
      id: `${Date.now()}-user`,
      variant: "user",
      content,
      timestamp: formatTime(new Date().toISOString()),
    };
    setMessages((prev) => [...prev, optimisticUserMessage]);

    try {
      if (!conversationId) {
        const created = await api.createConversation();
        conversationId = created.id;
        setActiveConversationId(conversationId);
      }

      const response = await api.sendMessage(content, conversationId);
      await refreshConversations(response.conversation_id);
      setActiveConversationId(response.conversation_id);

      setCapabilityUpdates((prev) =>
        prev.map((u) =>
          u.id === pendingUpdateId
            ? {
                ...u,
                status: "processing",
                message: "Queued for worker...",
                timestamp: "Just now",
              }
            : u,
        ),
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to send message";
      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-error`,
          variant: "system",
          content: `Message failed: ${msg}`,
          timestamp: formatTime(new Date().toISOString()),
        },
      ]);
      setCapabilityUpdates((prev) =>
        prev.map((u) =>
          u.id === pendingUpdateId
            ? { ...u, status: "error", message: msg, timestamp: "Just now" }
            : u,
        ),
      );
      toast.error(msg);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleNewChat = async () => {
    try {
      const created = await api.createConversation();
      setActiveConversationId(created.id);
      setMessages([]);
      await refreshConversations(created.id);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to create conversation";
      toast.error(msg);
    }
  };

  const handleSelectConversation = (id: string) => {
    void loadConversation(id);
  };

  const handleDeleteConversation = async (id: string) => {
    try {
      await api.deleteConversation(id);
      const list = await refreshConversations(activeConversationId);
      if (id === activeConversationId) {
        const next = list.find((c) => c.id !== id) || null;
        if (next) {
          await loadConversation(next.id);
        } else {
          setActiveConversationId(null);
          setMessages([]);
        }
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to delete conversation";
      toast.error(msg);
    }
  };

  const handleSavePromptComponent = async (componentId: string) => {
    try {
      await api.updatePromptComponent(componentId, {
        content: promptContentDrafts[componentId],
        enabled: promptEnabledDrafts[componentId],
      });
      await loadDashboardData();
      toast.success("Prompt component saved");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to save prompt component";
      toast.error(msg);
    }
  };

  const handleResetPrompts = async () => {
    try {
      await api.resetPrompts();
      await loadDashboardData();
      toast.success("Prompt profile reset to defaults");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to reset prompts";
      toast.error(msg);
    }
  };

  const handleSaveContextSettings = async () => {
    const maxResponseTokens = Number.parseInt(contextSettingsDraft.max_response_tokens, 10);
    const compactPct = Number.parseFloat(contextSettingsDraft.compact_trigger_pct);
    if (!Number.isFinite(maxResponseTokens) || maxResponseTokens < 16) {
      toast.error("Max response tokens must be at least 16");
      return;
    }
    if (!Number.isFinite(compactPct) || compactPct < 0.1 || compactPct > 1.0) {
      toast.error("Compact trigger must be between 0.1 and 1.0");
      return;
    }
    try {
      const updated = await api.updateContextSettings({
        max_response_tokens: maxResponseTokens,
        compact_trigger_pct: compactPct,
        compact_instructions: contextSettingsDraft.compact_instructions,
        memory_enabled: contextSettingsDraft.memory_enabled,
      });
      setContextSettings(updated);
      setContextSettingsDraft({
        max_response_tokens: String(updated.max_response_tokens),
        compact_trigger_pct: String(updated.compact_trigger_pct),
        compact_instructions: updated.compact_instructions || "",
        memory_enabled: updated.memory_enabled,
      });
      setMemoryEnabled(updated.memory_enabled);
      toast.success("Context settings saved");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to save context settings";
      toast.error(msg);
    }
  };

  const handleDeleteAllData = async () => {
    const confirmed = window.confirm(
      "Delete all local AIgentOS data? This cannot be undone.",
    );
    if (!confirmed) return;

    try {
      await api.deleteAllData();
      setMessages([]);
      setConversations([]);
      setActiveConversationId(null);
      setPerfHistory([]);
      setLatestPerf(EMPTY_PERF);
      setDebugLogs([]);
      setMemoryChunks([]);
      setInputResetSignal((v) => v + 1);
      await loadDashboardData();
      toast.success("All local data deleted");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to delete data";
      toast.error(msg);
    }
  };

  const handleExportData = async () => {
    try {
      const payload = await api.exportAllData();
      const now = new Date();
      const stamp = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
        "-",
        String(now.getHours()).padStart(2, "0"),
        String(now.getMinutes()).padStart(2, "0"),
        String(now.getSeconds()).padStart(2, "0"),
      ].join("");
      const filename = `aigentos-backup-${stamp}.json`;
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      toast.success(`Backup exported: ${filename}`);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to export backup";
      toast.error(msg);
    }
  };

  const handleReloadMemory = async () => {
    setMemoryLoading(true);
    try {
      const result = await api.getMemoryChunks(200);
      setMemoryChunks(result.chunks);
      setMemoryEnabled(result.memory_enabled);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to load memory";
      toast.error(msg);
    } finally {
      setMemoryLoading(false);
    }
  };

  const handleDeleteMemoryChunk = async (chunkId: string) => {
    try {
      await api.deleteMemoryChunk(chunkId);
      setMemoryChunks((prev) => prev.filter((chunk) => chunk.id !== chunkId));
      toast.success("Memory chunk deleted");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to delete memory chunk";
      toast.error(msg);
    }
  };

  const handleRunBaseline = async () => {
    if (baselineRunning) return;
    setBaselineRunning(true);
    setBaselineResult(null);
    setBaselineStatus(null);
    try {
      const started = await api.startBaseline({
        enforce_max_response_tokens: baselineEnforceMaxResponseTokens,
        mode: baselineMode,
      });
      setBaselineJobId(started.job_id);
      toast.success("Baseline started");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Failed to run baseline";
      toast.error(msg);
      setBaselineRunning(false);
    }
  };

  useEffect(() => {
    if (!baselineRunning || !baselineJobId) return;

    let cancelled = false;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const status = await api.getBaselineStatus(baselineJobId);
          if (cancelled) return;
          setBaselineStatus(status);
          if (status.status === "completed") {
            setBaselineResult(status.result ?? null);
            setBaselineRunning(false);
            toast.success("Baseline completed");
            clearInterval(timer);
          } else if (status.status === "failed") {
            setBaselineRunning(false);
            toast.error(status.error || "Baseline failed");
            clearInterval(timer);
          }
        } catch (error) {
          if (cancelled) return;
          setBaselineRunning(false);
          const msg = error instanceof Error ? error.message : "Failed to fetch baseline status";
          toast.error(msg);
          clearInterval(timer);
        }
      })();
    }, 1200);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [baselineRunning, baselineJobId]);

  const renderBaselineProgress = () => {
    if (!baselineStatus) return null;
    const total = Math.max(1, baselineStatus.total_calls || 1);
    const pct = Math.max(0, Math.min(100, Math.round((baselineStatus.completed_calls / total) * 100)));
    return (
      <div className="p-4 rounded-lg mb-6" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
        <div className="text-sm mb-2" style={{ color: "var(--aigent-color-text)" }}>
          Status: {baselineStatus.status} · {pct}%
        </div>
        <div className="w-full h-2 rounded" style={{ backgroundColor: "var(--aigent-color-bg)" }}>
          <div
            className="h-2 rounded"
            style={{ width: `${pct}%`, backgroundColor: "var(--aigent-color-primary)" }}
          />
        </div>
        <div className="text-xs mt-2" style={{ color: "var(--aigent-color-text-muted)" }}>
          {baselineStatus.current_step || "Running..."}
        </div>
        {baselineStatus.status === "failed" && baselineStatus.error && (
          <div className="text-xs mt-2" style={{ color: "var(--aigent-color-status-error)" }}>
            Error: {baselineStatus.error}
          </div>
        )}
        {baselineStatus.events.length > 0 && (
          <div className="mt-3 max-h-40 overflow-y-auto text-xs space-y-1" style={{ color: "var(--aigent-color-text-muted)" }}>
            {baselineStatus.events.slice(-12).map((evt, idx) => (
              <div key={`${idx}-${evt}`}>{evt}</div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const buildBaselineMarkdown = (result: ApiBaselineRunResponse) => {
    const lines: string[] = [];
    lines.push("## Baseline Results");
    lines.push("");
    lines.push(`- Model: ${result.model}`);
    lines.push(`- Mode: ${result.mode === "end_to_end_aigentos" ? "End-to-end AIgentOS" : "Direct model"}`);
    lines.push(`- Completed: ${new Date(result.completed_at).toLocaleString()}`);
    lines.push(`- Duration: ${formatMs(result.duration_ms)}`);
    if (result.mode === "end_to_end_aigentos") {
      lines.push(`- Note: E2E mode measures the real async AIgentOS path (chat enqueue -> worker -> assistant completion).`);
    }
    lines.push("");
    for (const category of result.categories) {
      lines.push(`### ${category.label}`);
      lines.push("");
      lines.push("| Case | User Input Est | Time to First Token | Time to Response Completion | Full Prompt Tokens | Completion Tokens | Total Tokens |");
      lines.push("|---|---:|---:|---:|---:|---:|---:|");
      for (const c of category.cases) {
        const ttft = c.ttft_ms ?? c.min_latency_ms ?? Math.round(c.avg_latency_ms);
        const completionTime = c.completion_time_ms ?? c.max_latency_ms ?? Math.round(c.avg_latency_ms);
        lines.push(
          `| ${c.label} | ${c.input_tokens_est} | ${formatMs(ttft)} | ${formatMs(completionTime)} | ${c.prompt_tokens} | ${c.completion_tokens} | ${c.total_tokens} |`,
        );
        if (c.per_turn_latency_ms && c.per_turn_latency_ms.length > 0) {
          lines.push("");
          lines.push("| Turn | In Tok | Out Tok | TTFT | Completion | Throughput |");
          lines.push("|---:|---:|---:|---:|---:|---:|");
          for (let i = 0; i < c.per_turn_latency_ms.length; i += 1) {
            const inTok = c.per_turn_prompt_tokens?.[i] ?? Math.round(c.prompt_tokens / Math.max(1, c.calls));
            const outTok = c.per_turn_completion_tokens?.[i] ?? Math.round(c.completion_tokens / Math.max(1, c.calls));
            const ttft = c.per_turn_ttft_ms?.[i] ?? c.per_turn_latency_ms[i];
            const completion = c.per_turn_latency_ms[i];
            const throughput = computeTokensPerSecond(outTok, completion);
            lines.push(`| ${i + 1} | ${inTok} | ${outTok} | ${ttft}ms | ${completion}ms | ${throughput != null ? `${throughput.toFixed(1)} tok/s` : "-"} |`);
          }
        }
      }
      lines.push("");
    }
    return `${lines.join("\n")}\n`;
  };

  const handleExportBaselineMarkdown = () => {
    if (!baselineResult) {
      toast.error("Run a baseline first");
      return;
    }
    const now = new Date();
    const stamp = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
      "-",
      String(now.getHours()).padStart(2, "0"),
      String(now.getMinutes()).padStart(2, "0"),
      String(now.getSeconds()).padStart(2, "0"),
    ].join("");
    const filename = `${baselineResult.mode === "end_to_end_aigentos" ? "baseline-e2e" : "baseline"}-${stamp}.md`;
    const blob = new Blob([buildBaselineMarkdown(baselineResult)], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    toast.success(`Exported ${filename}`);
  };

  const getInputContextError = (draft: string) => {
    const maxContext = contextSettings?.max_context_tokens ?? 4096;
    const compactTrigger = contextSettings?.compact_trigger_pct ?? 0.9;
    const compactInstructions = (contextSettings?.compact_instructions || "").trim();
    const history = messages
      .filter((m) => m.variant === "user" || m.variant === "agent")
      .map((m) => m.content)
      .join("\n");
    const trimmed = draft.trim();
    if (!history.trim() && !trimmed) return null;
    const draftPart = trimmed ? `\n${trimmed}` : "";

    // Track "previous total + current draft" for UX (conversation content only).
    const baseText = history.trim();
    let estimatedTokens = estimateTokens(`${baseText}${draftPart}`);
    const triggerTokens = Math.floor(maxContext * compactTrigger);
    if (compactInstructions && estimatedTokens >= triggerTokens) {
      const compactedText = [compactInstructions, history].filter(Boolean).join("\n");
      estimatedTokens = estimateTokens(`${compactedText}${draftPart}`);
    }

    if (estimatedTokens <= maxContext) return null;

    return `Estimated prompt context (${estimatedTokens} tokens) exceeds max context (${maxContext}). Reduce pasted text; otherwise older context will be dropped.`;
  };

  const getInputContextUsage = (draft: string) => {
    const maxContext = contextSettings?.max_context_tokens ?? 4096;
    const compactTrigger = contextSettings?.compact_trigger_pct ?? 0.9;
    const compactInstructions = (contextSettings?.compact_instructions || "").trim();
    const history = messages
      .filter((m) => m.variant === "user" || m.variant === "agent")
      .map((m) => m.content)
      .join("\n");
    const trimmed = draft.trim();
    if (!history.trim() && !trimmed) {
      return {
        estimatedTokens: 0,
        maxContext,
        pct: 0,
        exceeds: false,
        includesCompactInstructions: false,
      };
    }
    const draftPart = trimmed ? `\n${trimmed}` : "";

    // Track "previous total + current draft" for UX (conversation content only).
    const baseText = history.trim();
    let estimatedTokens = estimateTokens(`${baseText}${draftPart}`);
    const triggerTokens = Math.floor(maxContext * compactTrigger);
    let includesCompactInstructions = false;
    if (compactInstructions && estimatedTokens >= triggerTokens) {
      includesCompactInstructions = true;
      const compactedText = [compactInstructions, history].filter(Boolean).join("\n");
      estimatedTokens = estimateTokens(`${compactedText}${draftPart}`);
    }
    const pct = Math.min(999, Math.round((estimatedTokens / Math.max(1, maxContext)) * 100));
    return {
      estimatedTokens,
      maxContext,
      pct,
      exceeds: estimatedTokens > maxContext,
      includesCompactInstructions,
    };
  };

  const renderDashboardContent = () => {
    const ttftValues = perfHistory
      .map((item) => item.perf.ttft_ms)
      .filter((value): value is number => typeof value === "number" && value > 0);
    const avgTtftMs =
      ttftValues.length > 0
        ? Math.round(ttftValues.reduce((sum, value) => sum + value, 0) / ttftValues.length)
        : latestPerf.ttft_ms ?? 0;
    const throughputValues = perfHistory
      .map((item) => computeTokensPerSecond(item.perf.completion_tokens, item.perf.llm_latency_ms))
      .filter((value): value is number => typeof value === "number" && value > 0);
    const avgThroughput =
      throughputValues.length > 0
        ? throughputValues.reduce((sum, value) => sum + value, 0) / throughputValues.length
        : computeTokensPerSecond(latestPerf.completion_tokens, latestPerf.llm_latency_ms);
    const minThroughput = throughputValues.length > 0 ? Math.min(...throughputValues) : null;
    const maxThroughput = throughputValues.length > 0 ? Math.max(...throughputValues) : null;

    switch (dashboardSection) {
      case "performance":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8">
                <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>
                  Performance
                </h2>
                <p style={{ color: "var(--aigent-color-text-muted)" }}>
                  Monitor system performance and response times
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                  <div className="flex items-center gap-3 mb-4">
                    <Activity className="w-6 h-6" style={{ color: "var(--aigent-color-primary)" }} />
                    <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Response Timing</h3>
                  </div>
                  <div className="text-3xl font-medium mb-2" style={{ color: "var(--aigent-color-text)" }}>Live</div>
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Average TTFT {formatMs(avgTtftMs)} · Average Throughput {avgThroughput != null ? `${avgThroughput.toFixed(1)} tok/s` : "-"}
                  </p>
                  <p className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Historical throughput min {minThroughput != null ? `${minThroughput.toFixed(1)} tok/s` : "-"} · max {maxThroughput != null ? `${maxThroughput.toFixed(1)} tok/s` : "-"}
                  </p>
                </div>
                <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                  <div className="flex items-center gap-3 mb-4">
                    <Zap className="w-6 h-6" style={{ color: "var(--aigent-color-status-active)" }} />
                    <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Token Usage Total</h3>
                  </div>
                  <div className="text-3xl font-medium mb-2" style={{ color: "var(--aigent-color-text)" }}>
                    Today: {performanceSummary?.tokens_day.prompt_tokens ?? 0} (in) + {performanceSummary?.tokens_day.completion_tokens ?? 0} (out)
                  </div>
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Week: {performanceSummary?.tokens_week.prompt_tokens ?? 0} (in) + {performanceSummary?.tokens_week.completion_tokens ?? 0} (out)
                  </p>
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Month: {performanceSummary?.tokens_month.prompt_tokens ?? 0} (in) + {performanceSummary?.tokens_month.completion_tokens ?? 0} (out)
                  </p>
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    All-time: {performanceSummary?.tokens_all_time.prompt_tokens ?? 0} (in) + {performanceSummary?.tokens_all_time.completion_tokens ?? 0} (out)
                  </p>
                </div>
              </div>
              <div className="mt-6 p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <h3 className="m-0 mb-4" style={{ color: "var(--aigent-color-text)" }}>Recent Exchanges (Last 5)</h3>
                {perfHistory.length === 0 ? (
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    No exchanges yet.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {perfHistory.map((item) => (
                      <div
                        key={item.id}
                        className="p-4 rounded-lg"
                        style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)" }}
                      >
                        <div className="text-xs mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                          {formatTime(item.timestamp)} · conv {item.conversationId.slice(0, 8)}
                        </div>
                        <div className="text-sm mb-2" style={{ color: "var(--aigent-color-text)" }}>
                          <strong>U:</strong> {ellipsize(item.userPreview, 80)}
                        </div>
                        <div className="text-sm mb-3" style={{ color: "var(--aigent-color-text-muted)" }}>
                          <strong>A:</strong> {ellipsize(item.assistantPreview, 80)}
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                          <div>TTFT: {formatMs(item.perf.ttft_ms ?? 0)}</div>
                          <div>Completion: {formatMs(item.perf.total_latency_ms)}</div>
                          <div>Tokens/sec: {formatTokensPerSecond(item.perf.completion_tokens, item.perf.llm_latency_ms)}</div>
                          <div>Full prompt tokens: {item.perf.prompt_tokens ?? "-"}</div>
                          <div>Completion tokens: {item.perf.completion_tokens ?? "-"}</div>
                          <div>Retrieved chunks: {item.perf.retrieved_chunk_count ?? 0}</div>
                          <div>System tokens est: {item.perf.prompt_breakdown.system_tokens_est ?? "-"}</div>
                          <div>User tokens est: {item.perf.prompt_breakdown.user_tokens_est ?? "-"}</div>
                          <div>Assistant tokens est: {item.perf.prompt_breakdown.assistant_tokens_est ?? "-"}</div>
                          <div>Total tokens: {item.perf.total_tokens ?? "-"}</div>
                        </div>
                        {item.perf.retrieved_chunks && item.perf.retrieved_chunks.length > 0 && (
                          <div className="mt-3 space-y-2">
                            {item.perf.retrieved_chunks.map((chunk, idx) => (
                              <div
                                key={`${item.id}-chunk-${chunk.source_id}-${idx}`}
                                className="p-3 rounded"
                                style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}
                              >
                                <div className="text-xs mb-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                                  Retrieved memory {idx + 1} · score {chunk.score.toFixed(3)} · {chunk.source_type} · source {chunk.source_id.slice(0, 8)}
                                </div>
                                <div className="text-xs mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                                  Source preview: {chunk.source_preview || "(none)"}
                                </div>
                                <div className="text-xs whitespace-pre-wrap break-words" style={{ color: "var(--aigent-color-text)" }}>
                                  {chunk.content}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="mt-6 p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="flex items-center gap-3 mb-4">
                  <Brain className="w-6 h-6" style={{ color: "var(--aigent-color-primary)" }} />
                  <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Retrieved Memory</h3>
                </div>
                <div className="text-sm mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Latest response used {latestPerf.retrieved_chunk_count ?? 0} memory chunk{(latestPerf.retrieved_chunk_count ?? 0) === 1 ? "" : "s"}.
                </div>
                {latestPerf.retrieved_chunks && latestPerf.retrieved_chunks.length > 0 ? (
                  <div className="space-y-3">
                    {latestPerf.retrieved_chunks.map((chunk, idx) => (
                      <div
                        key={`retrieved-${chunk.source_id}-${idx}`}
                        className="p-4 rounded-lg"
                        style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)" }}
                      >
                        <div className="text-xs mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                          Chunk {idx + 1} · score {chunk.score.toFixed(3)} · {chunk.source_type} · source {chunk.source_id.slice(0, 8)}
                        </div>
                        <div className="text-xs mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                          Source preview: {chunk.source_preview || "(none)"}
                        </div>
                        <div className="text-sm whitespace-pre-wrap break-words" style={{ color: "var(--aigent-color-text)" }}>
                          {chunk.content}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    No memory chunks were retrieved for the latest response.
                  </p>
                )}
              </div>
            </div>
          </div>
        );
      case "database":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8">
                <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>Database</h2>
                <p style={{ color: "var(--aigent-color-text-muted)" }}>
                  SQLite chat history is persisted under models-local
                </p>
              </div>
              <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="flex items-center gap-3 mb-4">
                  <Database className="w-6 h-6" style={{ color: "var(--aigent-color-primary)" }} />
                  <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Chat Store</h3>
                </div>
                <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Backed by `/app/models-local/chat.db`
                </p>
              </div>
            </div>
          </div>
        );
      case "memory":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8 flex items-center justify-between">
                <div>
                  <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>Memory</h2>
                  <p style={{ color: "var(--aigent-color-text-muted)" }}>
                    Inspect and manage semantic memory chunks stored in `rag_chunks`.
                  </p>
                </div>
                <button
                  onClick={() => void handleReloadMemory()}
                  disabled={memoryLoading}
                  className="px-3 py-2 rounded-lg text-sm disabled:opacity-50"
                  style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                >
                  {memoryLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>

              <div className="p-6 rounded-lg mb-6" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="flex items-center gap-3 mb-4">
                  <Brain className="w-6 h-6" style={{ color: "var(--aigent-color-primary)" }} />
                  <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Memory Controls</h3>
                </div>
                <div
                  className="p-4 rounded-lg mb-4 text-sm space-y-2"
                  style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}
                >
                  <div>
                    Memory stores chunks from prior conversations and may retrieve them to influence future responses.
                  </div>
                  <div>
                    It is not a verified knowledge base, permanent profile system, or guaranteed source of truth.
                  </div>
                  <div>
                    Retrieved memory can help the model stay consistent across conversations, but it can also amplify earlier mistakes if incorrect assistant or user content is remembered and reused later.
                  </div>
                  <div>
                    If memory starts steering responses in the wrong direction, you can turn it off or delete individual chunks below.
                  </div>
                  <div>
                    To keep memory lightweight, older chunks are periodically rolled up into summarized memory when the store grows past its internal limit.
                  </div>
                </div>
                <label className="inline-flex items-center gap-3 text-sm" style={{ color: "var(--aigent-color-text)" }}>
                  <input
                    type="checkbox"
                    checked={contextSettingsDraft.memory_enabled}
                    onChange={(e) =>
                      setContextSettingsDraft((prev) => ({ ...prev, memory_enabled: e.target.checked }))
                    }
                  />
                  Enable memory retrieval and memory writes
                </label>
                <div className="text-xs mt-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Current status: {memoryEnabled ? "On" : "Off"}.
                </div>
                <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Turning memory off stops new retrievals and stops writing new chunks. Existing chunks remain until deleted.
                </div>
                <button
                  onClick={() => void handleSaveContextSettings()}
                  className="px-3 py-1 rounded text-sm mt-4"
                  style={{ backgroundColor: "var(--aigent-color-primary)", color: "#fff" }}
                >
                  Save Memory Setting
                </button>
              </div>

              <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>
                    Stored Chunks ({memoryChunks.length})
                  </h3>
                  <div className="text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Newest first
                  </div>
                </div>
                {memoryChunks.length === 0 ? (
                  <div className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    No memory chunks stored yet.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {memoryChunks.map((chunk) => (
                      <div
                        key={chunk.id}
                        className="p-4 rounded-lg"
                        style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)" }}
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <div className="text-xs mb-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                              {chunk.source_type} · source {chunk.source_id.slice(0, 8)} · {chunk.content_tokens_est} tok est · {chunk.embedding_dimensions} dims · {new Date(chunk.created_at).toLocaleString()}
                            </div>
                            <div className="text-sm whitespace-pre-wrap break-words" style={{ color: "var(--aigent-color-text)" }}>
                              {chunk.content}
                            </div>
                          </div>
                          <button
                            onClick={() => void handleDeleteMemoryChunk(chunk.id)}
                            className="px-3 py-2 rounded-lg text-sm flex items-center gap-2"
                            style={{ backgroundColor: "rgba(239, 68, 68, 0.12)", border: "1px solid rgba(239, 68, 68, 0.25)", color: "rgb(220, 38, 38)" }}
                          >
                            <Trash2 className="w-4 h-4" />
                            Delete
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      case "baseline":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8 flex items-center justify-between">
                <div>
                  <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>Baseline</h2>
                  <p style={{ color: "var(--aigent-color-text-muted)" }}>
                    Standardized benchmark runs for quality, latency, and token usage.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-sm flex items-center gap-2 mr-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Mode
                    <select
                      value={baselineMode}
                      disabled={baselineRunning}
                      onChange={(e) => setBaselineMode(e.target.value as "direct_model" | "end_to_end_aigentos")}
                      className="px-2 py-1 rounded"
                      style={{ backgroundColor: "var(--aigent-color-surface)", color: "var(--aigent-color-text)", border: "1px solid var(--aigent-color-border)" }}
                    >
                      <option value="direct_model">Direct model</option>
                      <option value="end_to_end_aigentos">End-to-end AIgentOS</option>
                    </select>
                  </label>
                  <label className="text-sm flex items-center gap-2 mr-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                    <input
                      type="checkbox"
                      checked={baselineEnforceMaxResponseTokens}
                      disabled={baselineRunning}
                      onChange={(e) => setBaselineEnforceMaxResponseTokens(e.target.checked)}
                    />
                    Enforce max response tokens
                  </label>
                  <button
                    onClick={() => void handleRunBaseline()}
                    disabled={baselineRunning}
                    className="px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                    style={{ backgroundColor: "var(--aigent-color-primary)", color: "#fff" }}
                  >
                    {baselineRunning ? "Running..." : "Run Baseline"}
                  </button>
                  <button
                    onClick={handleExportBaselineMarkdown}
                    disabled={!baselineResult}
                    className="px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                    style={{ backgroundColor: "var(--aigent-color-surface)", color: "var(--aigent-color-text)", border: "1px solid var(--aigent-color-border)" }}
                  >
                    Export .md
                  </button>
                </div>
              </div>
              <div className="p-4 rounded-lg mb-6" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="text-sm" style={{ color: "var(--aigent-color-text)" }}>
                  Includes:
                </div>
                <div className="text-xs mt-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Mode: {baselineMode === "end_to_end_aigentos" ? "End-to-end AIgentOS (real async worker path)" : "Direct model (raw prompt/model path)"}
                </div>
                <ul className="text-sm mt-2 space-y-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                  <li>Simple Q/A: 100, 250, 500 user tokens</li>
                  <li>Summarization: 200, 500, 1000, 2000 user tokens</li>
                  <li>Multi-turn: 20 turns, 50-200 user tokens per turn</li>
                  <li>System Prompt Pressure: ~200, ~500, ~1000, ~2000, ~5000, ~10000 target system tokens + estimated 100-300 user tokens</li>
                  <li>Extra: structured extraction reliability check</li>
                </ul>
              </div>
              {renderBaselineProgress()}

              {!baselineResult ? (
                <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}>
                  {baselineRunning
                    ? "Baseline is running"
                    : baselineStatus?.status === "failed"
                      ? `Baseline failed: ${baselineStatus.error || "Unknown error"}`
                      : "No baseline run yet."}
                </div>
              ) : (
                <div className="space-y-4">
                  {(() => {
                    const summary = summarizeBaseline(baselineResult);
                    return (
                      <div className="p-4 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                        <div className="text-sm" style={{ color: "var(--aigent-color-text)" }}>
                          Average TTFT {summary.avgTtftMs != null ? formatMs(summary.avgTtftMs) : "-"} · Average Throughput {summary.avgThroughput != null ? `${summary.avgThroughput.toFixed(1)} tok/s` : "-"}
                        </div>
                        <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                          Historical completion TTFT min {summary.minTtftMs != null ? formatMs(summary.minTtftMs) : "-"} · max {summary.maxTtftMs != null ? formatMs(summary.maxTtftMs) : "-"}
                        </div>
                        <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                          Historical completion throughput min {summary.minThroughput != null ? `${summary.minThroughput.toFixed(1)} tok/s` : "-"} · max {summary.maxThroughput != null ? `${summary.maxThroughput.toFixed(1)} tok/s` : "-"}
                        </div>
                      </div>
                    );
                  })()}
                  <div className="p-4 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                    <div className="text-sm" style={{ color: "var(--aigent-color-text)" }}>
                      Model: {baselineResult.model}
                    </div>
                    <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                      Mode: {baselineResult.mode === "end_to_end_aigentos" ? "End-to-end AIgentOS" : "Direct model"} · Duration: {formatMs(baselineResult.duration_ms)} · Completed: {new Date(baselineResult.completed_at).toLocaleString()}
                    </div>
                    <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                      Note: "User Input Est" is only the synthetic user payload estimate. "Full Prompt Tokens" is the actual model-reported total prompt size.
                    </div>
                    {baselineResult.mode === "end_to_end_aigentos" && (
                      <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                        E2E mode measures the real async AIgentOS path. System Prompt Pressure remains a direct-model synthetic stress test in 0.2.3-oss.
                      </div>
                    )}
                  </div>
                  {baselineResult.categories.map((category) => (
                    <div key={category.id} className="p-4 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                      <h3 className="m-0 mb-3" style={{ color: "var(--aigent-color-text)" }}>{category.label}</h3>
                      <div className="space-y-2">
                        {category.cases.map((c) => (
                          <div key={c.id} className="p-3 rounded" style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)" }}>
                            <div className="text-sm mb-1" style={{ color: "var(--aigent-color-text)" }}>{c.label}</div>
                            {(!c.per_turn_latency_ms || c.per_turn_latency_ms.length === 0) && (
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                                <div>User input est: {c.input_tokens_est}</div>
                                <div>Time to first token: {formatMs(c.ttft_ms ?? c.min_latency_ms ?? Math.round(c.avg_latency_ms))}</div>
                                <div>Time to response completion: {formatMs(c.completion_time_ms ?? c.max_latency_ms ?? Math.round(c.avg_latency_ms))}</div>
                                <div>Throughput: {formatTokensPerSecond(c.completion_tokens, c.completion_time_ms ?? c.total_latency_ms)}</div>
                                <div>Full prompt tokens: {c.prompt_tokens}</div>
                                <div>Completion tokens: {c.completion_tokens}</div>
                                <div>Total tokens: {c.total_tokens}</div>
                              </div>
                            )}
                            {c.per_turn_latency_ms && c.per_turn_latency_ms.length > 0 && (
                              <div className="mt-3 overflow-x-auto">
                                <table className="w-full text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                                  <thead>
                                    <tr>
                                      <th className="text-left py-1">Turn</th>
                                      <th className="text-left py-1">In tokens</th>
                                      <th className="text-left py-1">Out tokens</th>
                                      <th className="text-left py-1">TTFT</th>
                                      <th className="text-left py-1">Completion</th>
                                      <th className="text-left py-1">Throughput</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {c.per_turn_latency_ms.map((ms, idx) => (
                                      <tr key={`${c.id}-turn-${idx}`}>
                                        <td className="py-1">{idx + 1}</td>
                                        <td className="py-1">{c.per_turn_prompt_tokens?.[idx] ?? Math.round(c.prompt_tokens / Math.max(1, c.calls))}</td>
                                        <td className="py-1">{c.per_turn_completion_tokens?.[idx] ?? Math.round(c.completion_tokens / Math.max(1, c.calls))}</td>
                                        <td className="py-1">{formatMs(c.per_turn_ttft_ms?.[idx] ?? ms)}</td>
                                        <td className="py-1">{formatMs(ms)}</td>
                                        <td className="py-1">{formatTokensPerSecond(c.per_turn_completion_tokens?.[idx] ?? null, ms)}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      case "logs":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8">
                <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>System Logs</h2>
                <p style={{ color: "var(--aigent-color-text-muted)" }}>
                  Use `docker compose logs -f kernel` for live backend logs
                </p>
              </div>
              <div className="p-6 rounded-lg font-mono text-sm space-y-2" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}>
                <div>Kernel API + LLM integration is active.</div>
                <div>Conversation and message persistence enabled.</div>
              </div>
            </div>
          </div>
        );
      case "prompts":
        const isChangedProfile = Boolean(systemPrompt?.is_custom);
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8">
                <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>Prompts</h2>
                <p style={{ color: "var(--aigent-color-text-muted)" }}>
                  Read-only prompt components and composed system prompt.
                </p>
              </div>
              <div className="p-6 rounded-lg mb-6" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                <div className="flex flex-wrap gap-3 items-center mb-3">
                  <div className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Agent: {systemPrompt?.agent_id ?? "-"} · Components: {systemPrompt?.component_count ?? 0}
                  </div>
                  <span
                    className="px-2 py-1 rounded-full text-xs font-medium"
                    style={{
                      backgroundColor: isChangedProfile ? "rgba(245, 158, 11, 0.2)" : "rgba(34, 197, 94, 0.2)",
                      color: isChangedProfile ? "rgb(245, 158, 11)" : "rgb(34, 197, 94)",
                      border: `1px solid ${isChangedProfile ? "rgba(245, 158, 11, 0.4)" : "rgba(34, 197, 94, 0.4)"}`,
                    }}
                  >
                    {isChangedProfile ? "Changed" : "Default"}
                  </span>
                  <button
                    onClick={() => void handleResetPrompts()}
                    className="px-3 py-1 rounded text-sm"
                    style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                  >
                    Reset to Default
                  </button>
                </div>
                <pre
                  className="text-xs whitespace-pre-wrap overflow-y-auto max-h-72 p-3 rounded"
                  style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}
                >
                  {systemPrompt?.prompt ?? "No system prompt loaded."}
                </pre>
              </div>
              <div className="space-y-3">
                {promptComponents.map((component) => (
                  <div
                    key={component.id}
                    className="p-4 rounded-lg"
                    style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <div className="text-sm font-medium" style={{ color: "var(--aigent-color-text)" }}>
                          {component.name}
                        </div>
                        <div
                          className="text-xs"
                          style={{
                            color: "var(--aigent-color-text-muted)",
                            fontFamily: "var(--aigent-font-mono)",
                            wordBreak: "break-all",
                          }}
                        >
                          {component.file_path}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span
                          className="px-2 py-1 rounded-full text-xs font-medium"
                          style={{
                            backgroundColor: component.is_custom ? "rgba(245, 158, 11, 0.2)" : "rgba(34, 197, 94, 0.2)",
                            color: component.is_custom ? "rgb(245, 158, 11)" : "rgb(34, 197, 94)",
                            border: `1px solid ${component.is_custom ? "rgba(245, 158, 11, 0.4)" : "rgba(34, 197, 94, 0.4)"}`,
                          }}
                        >
                          {component.is_custom ? "custom" : "default"}
                        </span>
                        <label className="text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                          <input
                            type="checkbox"
                            checked={promptEnabledDrafts[component.id] ?? component.enabled}
                            onChange={(e) =>
                              setPromptEnabledDrafts((prev) => ({ ...prev, [component.id]: e.target.checked }))
                            }
                          />{" "}
                          enabled
                        </label>
                      </div>
                    </div>
                    <textarea
                      value={promptContentDrafts[component.id] ?? component.content}
                      onChange={(e) =>
                        setPromptContentDrafts((prev) => ({ ...prev, [component.id]: e.target.value }))
                      }
                      className="w-full text-xs whitespace-pre-wrap overflow-y-auto max-h-64 p-3 rounded"
                      style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}
                      rows={8}
                    />
                    <div className="mt-2">
                      <button
                        onClick={() => void handleSavePromptComponent(component.id)}
                        className="px-3 py-1 rounded text-sm"
                        style={{ backgroundColor: "var(--aigent-color-primary)", color: "#fff" }}
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div
                className="p-4 rounded-lg mt-6"
                style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}
              >
                <h3 className="m-0 mb-3" style={{ color: "var(--aigent-color-text)" }}>
                  Context Compaction Settings
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-3">
                  <label className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Max model context window (tokens)
                    <input
                      type="number"
                      value={String(contextSettings?.max_context_tokens ?? 4096)}
                      disabled
                      className="w-full mt-1 px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}
                    />
                  </label>
                  <label className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Max response tokens
                    <input
                      type="number"
                      min={16}
                      step={1}
                      value={contextSettingsDraft.max_response_tokens}
                      onChange={(e) =>
                        setContextSettingsDraft((prev) => ({ ...prev, max_response_tokens: e.target.value }))
                      }
                      className="w-full mt-1 px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                    />
                  </label>
                  <label className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Compact context trigger (0.1-1.0)
                    <input
                      type="number"
                      min={0.1}
                      max={1.0}
                      step={0.01}
                      value={contextSettingsDraft.compact_trigger_pct}
                      onChange={(e) =>
                        setContextSettingsDraft((prev) => ({ ...prev, compact_trigger_pct: e.target.value }))
                      }
                      className="w-full mt-1 px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                    />
                  </label>
                  <label className="text-sm flex items-center gap-2 md:col-span-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                    <input
                      type="checkbox"
                      checked={contextSettingsDraft.memory_enabled}
                      onChange={(e) =>
                        setContextSettingsDraft((prev) => ({ ...prev, memory_enabled: e.target.checked }))
                      }
                    />
                    Enable memory retrieval and memory writes
                  </label>
                </div>
                <label className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Special compact context instructions
                  <textarea
                    value={contextSettingsDraft.compact_instructions}
                    onChange={(e) =>
                      setContextSettingsDraft((prev) => ({ ...prev, compact_instructions: e.target.value }))
                    }
                    rows={5}
                    className="w-full mt-1 px-3 py-2 rounded"
                    style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                  />
                </label>
                <div className="text-xs mt-2" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Current: {contextSettings?.max_context_tokens ?? 4096} context tokens · {contextSettings?.max_response_tokens ?? 512} response tokens · trigger {(contextSettings?.compact_trigger_pct ?? 0.9) * 100}% · memory {contextSettings?.memory_enabled ? "on" : "off"}
                </div>
                <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                  Verification: Agent Updates will show "Context compacted" when compaction is applied.
                </div>
                <button
                  onClick={() => void handleSaveContextSettings()}
                  className="px-3 py-1 rounded text-sm mt-3"
                  style={{ backgroundColor: "var(--aigent-color-primary)", color: "#fff" }}
                >
                  Save Context Settings
                </button>
              </div>
            </div>
          </div>
        );
      case "debug":
        return (
          <div className="h-full overflow-y-auto p-8">
            <div className="max-w-7xl mx-auto">
              <div className="mb-8 flex items-center justify-between">
                <div>
                  <h2 className="mb-2" style={{ color: "var(--aigent-color-text)" }}>Debug Logs</h2>
                  <p style={{ color: "var(--aigent-color-text-muted)" }}>
                    Recent exchange debug records.
                  </p>
                </div>
                <button
                  onClick={() => void loadDashboardData()}
                  className="px-3 py-2 rounded-lg text-sm"
                  style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text)" }}
                >
                  Refresh
                </button>
              </div>
              <div className="space-y-3">
                {debugLogs.length === 0 ? (
                  <div className="p-6 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}>
                    No debug logs yet.
                  </div>
                ) : (
                  debugLogs.map((log) => {
                    const expanded = Boolean(expandedDebugIds[log.id]);
                    return (
                      <div
                        key={log.id}
                        className="p-4 rounded-lg"
                        style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}
                      >
                        <button
                          onClick={() =>
                            setExpandedDebugIds((prev) => ({ ...prev, [log.id]: !expanded }))
                          }
                          className="w-full text-left"
                        >
                          <div className="text-sm" style={{ color: "var(--aigent-color-text)" }}>
                            {log.log_type}
                          </div>
                          <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                            {new Date(log.created_at).toLocaleString()} · {log.duration_ms ?? "-"}ms · {log.token_count ?? "-"} tokens
                          </div>
                        </button>
                        {expanded && (
                          <pre
                            className="text-xs whitespace-pre-wrap overflow-y-auto max-h-64 p-3 rounded mt-3"
                            style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)", color: "var(--aigent-color-text-muted)" }}
                          >
                            {JSON.stringify(log.content, null, 2)}
                          </pre>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        );
    }
  };

  const renderedMessages =
    messages.length > 0
      ? messages
      : [
          {
            id: "empty-system",
            variant: "system" as const,
            content: "Start a conversation to chat with your local language model.",
            timestamp: formatTime(new Date().toISOString()),
          },
        ];

  return (
    <div className="h-screen flex flex-col" style={{ backgroundColor: "var(--aigent-color-bg)", fontFamily: "var(--aigent-font-sans)" }}>
      <Toaster position="bottom-right" />

      <Header
        agentName="Atlas"
        currentView={currentView}
        onViewChange={setCurrentView}
        onSettingsClick={() => setSettingsOpen(true)}
      />

      <div className="flex-1 flex overflow-hidden">
        {currentView === "chat" ? (
          <ConversationsSidebar
            conversations={conversations}
            onNewChat={handleNewChat}
            onSelectConversation={handleSelectConversation}
            onDeleteConversation={handleDeleteConversation}
            isCollapsed={conversationsSidebarCollapsed}
            onToggleCollapse={() => setConversationsSidebarCollapsed(!conversationsSidebarCollapsed)}
          />
        ) : (
          <DashboardSidebar
            activeSection={dashboardSection}
            onSectionChange={setDashboardSection}
            isCollapsed={dashboardSidebarCollapsed}
            onToggleCollapse={() => setDashboardSidebarCollapsed(!dashboardSidebarCollapsed)}
          />
        )}

        <main className="flex-1 flex flex-col overflow-hidden">
          {currentView === "dashboard" ? (
            renderDashboardContent()
          ) : (
            <div className="flex-1 overflow-y-auto px-6 py-6">
              {renderedMessages.map((message) => (
                <MessageBubble
                  key={message.id}
                  variant={message.variant}
                  timestamp={message.timestamp}
                  reasoning={message.reasoning}
                  capabilityName={message.capabilityName}
                  capabilityIcon={message.capabilityName === "LLM" ? <MessageSquare className="w-4 h-4" /> : undefined}
                >
                  {message.content}
                </MessageBubble>
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}

          {currentView === "chat" && (
            <InputArea
              onSend={handleSendMessage}
              isProcessing={isProcessing}
              resetSignal={inputResetSignal}
              validateMessage={getInputContextError}
              getUsage={getInputContextUsage}
            />
          )}
        </main>

        {currentView === "chat" && (
          <CapabilityUpdatesPanel
            updates={capabilityUpdates}
            isCollapsed={updatesPanelCollapsed}
            onToggleCollapse={() => setUpdatesPanelCollapsed(!updatesPanelCollapsed)}
          />
        )}
      </div>

      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        modelName={runtimeModel}
        backendEndpoint={apiEndpoint}
        healthEndpoint={`${apiEndpoint}/health`}
        backendConnected={backendConnected}
        backendReady={isModelWarm}
        onExportData={handleExportData}
        onDeleteAllData={handleDeleteAllData}
      />
      <Footer />
    </div>
  );
}
