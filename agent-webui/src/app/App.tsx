import { useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import { Activity, Bot, Cpu, Database, MessageSquare, Zap } from "lucide-react";

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

const EMPTY_PERF: ApiPerformanceMetrics = {
  total_latency_ms: 0,
  llm_latency_ms: 0,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
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
  const [telemetryMissingWarned, setTelemetryMissingWarned] = useState(false);
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
  }>({
    max_response_tokens: "512",
    compact_trigger_pct: "0.9",
    compact_instructions: "",
  });
  const [debugLogs, setDebugLogs] = useState<ApiDebugLog[]>([]);
  const [expandedDebugIds, setExpandedDebugIds] = useState<Record<string, boolean>>({});
  const [inputResetSignal, setInputResetSignal] = useState(0);
  const [baselineRunning, setBaselineRunning] = useState(false);
  const [baselineResult, setBaselineResult] = useState<ApiBaselineRunResponse | null>(null);
  const [baselineStatus, setBaselineStatus] = useState<ApiBaselineJobStatusResponse | null>(null);
  const [baselineJobId, setBaselineJobId] = useState<string | null>(null);
  const [baselineEnforceMaxResponseTokens, setBaselineEnforceMaxResponseTokens] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

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
    const detail = await api.getConversation(conversationId);
    setActiveConversationId(conversationId);
    setMessages(detail.messages.map(mapApiMessage));
    await refreshConversations(conversationId);
  };

  const loadDashboardData = async () => {
    const [promptData, componentsData, summaryData, contextData, debugData] = await Promise.all([
      api.getSystemPrompt(),
      api.getPromptComponents(),
      api.getPerformanceSummary(),
      api.getContextSettings(),
      api.getDebugLogs(50),
    ]);
    setSystemPrompt(promptData);
    setPromptComponents(componentsData);
    setPerformanceSummary(summaryData);
    setContextSettings(contextData);
    setContextSettingsDraft({
      max_response_tokens: String(contextData.max_response_tokens),
      compact_trigger_pct: String(contextData.compact_trigger_pct),
      compact_instructions: contextData.compact_instructions || "",
    });
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
    if (dashboardSection === "prompts" || dashboardSection === "debug") {
      void loadDashboardData();
    }
  }, [currentView, dashboardSection]);

  useEffect(() => {
    if (currentView !== "chat") return;
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isProcessing, currentView]);

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
      const hasTelemetry = Boolean(response.performance);
      const perf = response.performance ?? EMPTY_PERF;
      setMessages((prev) => [
        ...prev,
        mapApiMessage(response.assistant_message),
      ]);
      setLatestPerf(perf);
      setPerfHistory((prev) => [
        {
          id: `${response.assistant_message.id}-perf`,
          conversationId: response.conversation_id,
          timestamp: response.assistant_message.timestamp,
          userPreview: ellipsize(content, 80),
          assistantPreview: ellipsize(parseAssistantContent(response.assistant_message.content).visible, 80),
          perf,
        },
        ...prev,
      ].slice(0, 5));

      await refreshConversations(response.conversation_id);
      const freshSummary = await api.getPerformanceSummary();
      setPerformanceSummary(freshSummary);

      setCapabilityUpdates((prev) =>
        prev.map((u) =>
          u.id === pendingUpdateId
            ? {
                ...u,
                status: "success",
                message: hasTelemetry
                  ? (() => {
                      const compaction = perf.context_compaction;
                      if (compaction?.applied) {
                        return `Done in ${formatMs(perf.total_latency_ms)} · Context compacted (${compaction.dropped_history_messages} old messages dropped)`;
                      }
                      return `Done in ${formatMs(perf.total_latency_ms)}`;
                    })()
                  : "Response generated (perf telemetry missing from backend)",
                timestamp: "Just now",
              }
            : u,
        ),
      );
      if (!hasTelemetry && !telemetryMissingWarned) {
        toast.warning("Perf telemetry missing in API response. Rebuild/restart kernel container.");
        setTelemetryMissingWarned(true);
      }
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
      });
      setContextSettings(updated);
      setContextSettingsDraft({
        max_response_tokens: String(updated.max_response_tokens),
        compact_trigger_pct: String(updated.compact_trigger_pct),
        compact_instructions: updated.compact_instructions || "",
      });
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

  const handleRunBaseline = async () => {
    if (baselineRunning) return;
    setBaselineRunning(true);
    setBaselineResult(null);
    setBaselineStatus(null);
    try {
      const started = await api.startBaseline({
        enforce_max_response_tokens: baselineEnforceMaxResponseTokens,
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
          Status: {baselineStatus.status} · {baselineStatus.completed_calls}/{baselineStatus.total_calls} calls ({pct}%)
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
    lines.push(`- Completed: ${new Date(result.completed_at).toLocaleString()}`);
    lines.push(`- Duration: ${formatMs(result.duration_ms)}`);
    lines.push(`- Total Calls: ${result.total_calls}`);
    lines.push("");
    for (const category of result.categories) {
      lines.push(`### ${category.label}`);
      lines.push("");
      lines.push("| Case | Calls | Input Est | Min Latency | Max Latency | Avg Latency | Prompt Tok | Completion Tok | Total Tok |");
      lines.push("|---|---:|---:|---:|---:|---:|---:|---:|---:|");
      for (const c of category.cases) {
        const minLatency = c.min_latency_ms ?? Math.round(c.avg_latency_ms);
        const maxLatency = c.max_latency_ms ?? Math.round(c.avg_latency_ms);
        lines.push(
          `| ${c.label} | ${c.calls} | ${c.input_tokens_est} | ${formatMs(minLatency)} | ${formatMs(maxLatency)} | ${formatMs(Math.round(c.avg_latency_ms))} | ${c.prompt_tokens} | ${c.completion_tokens} | ${c.total_tokens} |`,
        );
        if (c.per_turn_latency_ms && c.per_turn_latency_ms.length > 0) {
          lines.push("");
          lines.push("| Turn | In Tok | Out Tok | Latency |");
          lines.push("|---:|---:|---:|---:|");
          for (let i = 0; i < c.per_turn_latency_ms.length; i += 1) {
            const inTok = c.per_turn_prompt_tokens?.[i] ?? Math.round(c.prompt_tokens / Math.max(1, c.calls));
            const outTok = c.per_turn_completion_tokens?.[i] ?? Math.round(c.completion_tokens / Math.max(1, c.calls));
            const latency = c.per_turn_latency_ms[i];
            lines.push(`| ${i + 1} | ${inTok} | ${outTok} | ${latency}ms |`);
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
    const filename = `baseline-${stamp}.md`;
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
                    <h3 className="m-0" style={{ color: "var(--aigent-color-text)" }}>Response Time</h3>
                  </div>
                  <div className="text-3xl font-medium mb-2" style={{ color: "var(--aigent-color-text)" }}>Live</div>
                  <p className="text-sm" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Total {formatMs(latestPerf.total_latency_ms)} · LLM {formatMs(latestPerf.llm_latency_ms)}
                  </p>
                  <p className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                    Min {formatMs(performanceSummary?.latency_min_ms ?? 0)} · Max {formatMs(performanceSummary?.latency_max_ms ?? 0)} · Avg {formatMs(Math.round(performanceSummary?.latency_avg_ms ?? 0))}
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
                          <div>Total: {formatMs(item.perf.total_latency_ms)}</div>
                          <div>LLM: {formatMs(item.perf.llm_latency_ms)}</div>
                          <div>In tokens: {item.perf.prompt_tokens ?? "-"}</div>
                          <div>Out tokens: {item.perf.completion_tokens ?? "-"}</div>
                          <div>System tokens est: {item.perf.prompt_breakdown.system_tokens_est ?? "-"}</div>
                          <div>User tokens est: {item.perf.prompt_breakdown.user_tokens_est ?? "-"}</div>
                          <div>Assistant tokens est: {item.perf.prompt_breakdown.assistant_tokens_est ?? "-"}</div>
                          <div>Total tokens: {item.perf.total_tokens ?? "-"}</div>
                        </div>
                      </div>
                    ))}
                  </div>
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
                <ul className="text-sm mt-2 space-y-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                  <li>Simple Q/A: 100, 250, 500 user tokens</li>
                  <li>Summarization: 200, 500, 1000, 2000 user tokens</li>
                  <li>Multi-turn: 20 turns, 50-200 user tokens per turn</li>
                  <li>System Prompt Pressure: 200, 500, 1000, 2000, 5000, 10000 system tokens + 100-300 user tokens</li>
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
                  <div className="p-4 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                    <div className="text-sm" style={{ color: "var(--aigent-color-text)" }}>
                      Model: {baselineResult.model}
                    </div>
                    <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                      Duration: {formatMs(baselineResult.duration_ms)} · Calls: {baselineResult.total_calls} · Completed: {new Date(baselineResult.completed_at).toLocaleString()}
                    </div>
                    <div className="text-xs mt-1" style={{ color: "var(--aigent-color-text-muted)" }}>
                      Note: Multi-turn latency reports min/max/avg across turns.
                    </div>
                  </div>
                  {baselineResult.categories.map((category) => (
                    <div key={category.id} className="p-4 rounded-lg" style={{ backgroundColor: "var(--aigent-color-surface)", border: "1px solid var(--aigent-color-border)" }}>
                      <h3 className="m-0 mb-3" style={{ color: "var(--aigent-color-text)" }}>{category.label}</h3>
                      <div className="space-y-2">
                        {category.cases.map((c) => (
                          <div key={c.id} className="p-3 rounded" style={{ backgroundColor: "var(--aigent-color-bg)", border: "1px solid var(--aigent-color-border)" }}>
                            <div className="text-sm mb-1" style={{ color: "var(--aigent-color-text)" }}>{c.label}</div>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                              <div>Calls: {c.calls}</div>
                              <div>Input est: {c.input_tokens_est}</div>
                              <div>Min latency: {formatMs(c.min_latency_ms ?? Math.round(c.avg_latency_ms))}</div>
                              <div>Max latency: {formatMs(c.max_latency_ms ?? Math.round(c.avg_latency_ms))}</div>
                              <div>Avg latency: {formatMs(Math.round(c.avg_latency_ms))}</div>
                              <div>Prompt tokens: {c.prompt_tokens}</div>
                              <div>Completion tokens: {c.completion_tokens}</div>
                              <div>Total tokens: {c.total_tokens}</div>
                            </div>
                            {c.per_turn_latency_ms && c.per_turn_latency_ms.length > 0 && (
                              <div className="mt-3 overflow-x-auto">
                                <table className="w-full text-xs" style={{ color: "var(--aigent-color-text-muted)" }}>
                                  <thead>
                                    <tr>
                                      <th className="text-left py-1">Turn</th>
                                      <th className="text-left py-1">In tokens</th>
                                      <th className="text-left py-1">Out tokens</th>
                                      <th className="text-left py-1">Latency</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {c.per_turn_latency_ms.map((ms, idx) => (
                                      <tr key={`${c.id}-turn-${idx}`}>
                                        <td className="py-1">{idx + 1}</td>
                                        <td className="py-1">{c.per_turn_prompt_tokens?.[idx] ?? Math.round(c.prompt_tokens / Math.max(1, c.calls))}</td>
                                        <td className="py-1">{c.per_turn_completion_tokens?.[idx] ?? Math.round(c.completion_tokens / Math.max(1, c.calls))}</td>
                                        <td className="py-1">{formatMs(ms)}</td>
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
                  Current: {contextSettings?.max_context_tokens ?? 4096} context tokens · {contextSettings?.max_response_tokens ?? 512} response tokens · trigger {(contextSettings?.compact_trigger_pct ?? 0.9) * 100}%
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
