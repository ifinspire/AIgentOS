from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import uuid


def default_compaction_instructions() -> str:
    return (
        "If the conversation is approaching context limits, prioritize preserving the user's latest objective, "
        "decisions, constraints, and unresolved questions. Summarize older turns into concise bullet points and "
        "drop low-value details, while keeping critical facts and commitments intact."
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_from_iso(value: str) -> datetime:
    value = value.replace("\x00", "").strip()
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


@dataclass
class StoredMessage:
    id: str
    role: str
    content: str
    created_at: datetime


@dataclass
class StoredConversation:
    id: str
    title: str
    updated_at: datetime
    last_message: str
    message_count: int


@dataclass
class StoredInteractionEvent:
    id: str
    conversation_id: str
    role: str
    event_type: str
    content: str
    status: str
    created_at: datetime
    processed_at: datetime | None
    error: str | None
    causation_event_id: str | None


@dataclass
class StoredRagChunk:
    id: str
    source_type: str
    source_id: str
    content: str
    created_at: datetime
    embedding: list[float]


@dataclass
class StoredRetrievedChunk:
    content: str
    score: float
    source_id: str
    source_type: str
    source_preview: str


@dataclass
class StoredPerformanceExchange:
    id: str
    conversation_id: str
    user_event_id: str | None
    assistant_event_id: str | None
    created_at: datetime
    user_preview: str
    assistant_preview: str
    total_latency_ms: int
    llm_latency_ms: int
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    response_source: str | None
    response_policy: str | None
    llm_involved: bool
    tool_observations: list[dict]
    workflow_trace: list[dict]
    retrieved_chunk_count: int
    retrieved_chunks: list[StoredRetrievedChunk]
    system_chars: int
    user_chars: int
    assistant_chars: int
    system_tokens_est: int | None
    user_tokens_est: int | None
    assistant_tokens_est: int | None


@dataclass
class StoredPromptProfile:
    id: str
    tenant_id: str
    name: str
    is_default: bool
    is_active: bool
    updated_at: datetime


@dataclass
class StoredContextSettings:
    tenant_id: str
    max_context_tokens: int
    max_response_tokens: int
    compact_trigger_pct: float
    compact_instructions: str
    memory_enabled: bool
    updated_at: datetime


@dataclass
class StoredOrchestrationEvent:
    id: str
    conversation_id: str | None
    parent_event_id: str | None
    document_id: str | None
    event_type: str
    label: str
    detail: str | None
    status: str
    payload: dict
    created_at: datetime
    processed_at: datetime | None
    error: str | None


@dataclass
class StoredTurnContext:
    user_event_id: str
    conversation_id: str
    route_decision: str
    retrieved_chunks: list[StoredRetrievedChunk]
    tool_observations: list[dict]
    memory_candidates: list[dict]
    created_at: datetime
    updated_at: datetime


@dataclass
class StoredDocumentImport:
    id: str
    conversation_id: str | None
    filename: str
    media_type: str
    file_hash: str | None
    stored_path: str
    status: str
    created_at: datetime
    processed_at: datetime | None
    error: str | None


@dataclass
class StoredMcpServer:
    id: str
    name: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    env: dict[str, str]
    enabled: bool
    status: str
    last_error: str | None
    discovered_tools: list[dict]
    created_at: datetime
    updated_at: datetime


class ChatStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = self._create_connection()
        try:
            self._init_db()
            self._repair_db_if_needed()
        except sqlite3.DatabaseError:
            self._rebuild_database_file()
            self._conn = self._create_connection()
            self._init_db()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _rebuild_database_file(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        corrupt_path = f"{self._db_path}.corrupt-{timestamp}"
        if os.path.exists(self._db_path):
            os.replace(self._db_path, corrupt_path)
        wal_path = f"{self._db_path}-wal"
        shm_path = f"{self._db_path}-shm"
        for path in (wal_path, shm_path):
            if os.path.exists(path):
                os.remove(path)

    def _init_db(self) -> None:
        with self._conn as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    last_message_preview TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS interaction_events (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    error TEXT,
                    causation_event_id TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_interaction_events_status_created
                ON interaction_events(status, created_at);

                CREATE INDEX IF NOT EXISTS idx_interaction_events_conversation_created
                ON interaction_events(conversation_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_interaction_events_causation
                ON interaction_events(causation_event_id);

                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_rag_chunks_source
                ON rag_chunks(source_type, source_id);

                CREATE TABLE IF NOT EXISTS performance_exchanges (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_event_id TEXT,
                    assistant_event_id TEXT,
                    created_at TEXT NOT NULL,
                    user_preview TEXT NOT NULL,
                    assistant_preview TEXT NOT NULL,
                    total_latency_ms INTEGER NOT NULL,
                    llm_latency_ms INTEGER NOT NULL,
                    ttft_ms INTEGER,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    response_source TEXT,
                    response_policy TEXT,
                    llm_involved INTEGER NOT NULL DEFAULT 1,
                    tool_observations TEXT NOT NULL DEFAULT '[]',
                    workflow_trace TEXT NOT NULL DEFAULT '[]',
                    retrieved_chunk_count INTEGER NOT NULL DEFAULT 0,
                    retrieved_chunks TEXT NOT NULL DEFAULT '[]',
                    system_chars INTEGER NOT NULL,
                    user_chars INTEGER NOT NULL,
                    assistant_chars INTEGER NOT NULL,
                    system_tokens_est INTEGER,
                    user_tokens_est INTEGER,
                    assistant_tokens_est INTEGER,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS worker_heartbeat (
                    worker_id TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_profiles (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_component_overrides (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    component_id TEXT NOT NULL,
                    content TEXT,
                    enabled INTEGER,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES prompt_profiles(id),
                    UNIQUE(profile_id, component_id)
                );

                CREATE TABLE IF NOT EXISTS prompt_context_settings (
                    tenant_id TEXT PRIMARY KEY,
                    max_context_tokens INTEGER NOT NULL,
                    max_response_tokens INTEGER NOT NULL DEFAULT 512,
                    compact_trigger_pct REAL NOT NULL,
                    compact_instructions TEXT NOT NULL DEFAULT '',
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestration_events (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    parent_event_id TEXT,
                    document_id TEXT,
                    event_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    detail TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    error TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_event_id) REFERENCES interaction_events(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_orchestration_events_status_created
                ON orchestration_events(status, created_at);

                CREATE INDEX IF NOT EXISTS idx_orchestration_events_conversation_created
                ON orchestration_events(conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS turn_contexts (
                    user_event_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    route_decision TEXT NOT NULL DEFAULT 'pending',
                    retrieved_chunks TEXT NOT NULL DEFAULT '[]',
                    tool_observations TEXT NOT NULL DEFAULT '[]',
                    memory_candidates TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_event_id) REFERENCES interaction_events(id) ON DELETE CASCADE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS document_imports (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    file_hash TEXT,
                    stored_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    error TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    command TEXT,
                    args TEXT NOT NULL DEFAULT '[]',
                    url TEXT,
                    env TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'configured',
                    last_error TEXT,
                    discovered_tools TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            convo_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "message_count" not in convo_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0")
            if "last_message_preview" not in convo_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN last_message_preview TEXT NOT NULL DEFAULT ''")
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(performance_exchanges)").fetchall()
            }
            if "ttft_ms" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN ttft_ms INTEGER")
            if "user_event_id" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN user_event_id TEXT")
            if "assistant_event_id" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN assistant_event_id TEXT")
            if "response_source" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN response_source TEXT")
            if "response_policy" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN response_policy TEXT")
            if "llm_involved" not in columns:
                conn.execute("ALTER TABLE performance_exchanges ADD COLUMN llm_involved INTEGER NOT NULL DEFAULT 1")
            if "tool_observations" not in columns:
                conn.execute(
                    "ALTER TABLE performance_exchanges ADD COLUMN tool_observations TEXT NOT NULL DEFAULT '[]'"
                )
            if "workflow_trace" not in columns:
                conn.execute(
                    "ALTER TABLE performance_exchanges ADD COLUMN workflow_trace TEXT NOT NULL DEFAULT '[]'"
                )
            if "retrieved_chunk_count" not in columns:
                conn.execute(
                    "ALTER TABLE performance_exchanges ADD COLUMN retrieved_chunk_count INTEGER NOT NULL DEFAULT 0"
                )
            if "retrieved_chunks" not in columns:
                conn.execute(
                    "ALTER TABLE performance_exchanges ADD COLUMN retrieved_chunks TEXT NOT NULL DEFAULT '[]'"
                )
            context_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(prompt_context_settings)").fetchall()
            }
            if "memory_enabled" not in context_columns:
                conn.execute(
                    "ALTER TABLE prompt_context_settings ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1"
                )

    def _delete_rows_with_null_bytes(self, table: str, columns: list[str]) -> int:
        predicates = [f"substr(hex(COALESCE({column}, '')), 1, 2) = '00'" for column in columns]
        if not predicates:
            return 0
        query = f"DELETE FROM {table} WHERE " + " OR ".join(predicates)
        cursor = self._conn.execute(query)
        return int(cursor.rowcount or 0)

    def _repair_db_if_needed(self) -> None:
        try:
            integrity = self._conn.execute("PRAGMA integrity_check").fetchone()
            integrity_status = str(integrity[0]) if integrity is not None else "ok"
        except sqlite3.DatabaseError:
            integrity_status = "error"
        deleted = 0
        deleted += self._delete_rows_with_null_bytes(
            "interaction_events",
            ["status", "created_at", "processed_at", "causation_event_id"],
        )
        deleted += self._delete_rows_with_null_bytes(
            "orchestration_events",
            ["status", "created_at", "processed_at", "parent_event_id", "document_id"],
        )
        deleted += self._delete_rows_with_null_bytes(
            "conversations",
            ["created_at", "updated_at"],
        )
        if integrity_status != "ok" or deleted > 0:
            self._conn.execute("REINDEX")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
            document_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(document_imports)").fetchall()
            }
            if "file_hash" not in document_columns:
                conn.execute("ALTER TABLE document_imports ADD COLUMN file_hash TEXT")
            turn_context_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(turn_contexts)").fetchall()
            }
            if "route_decision" not in turn_context_columns and turn_context_columns:
                conn.execute("ALTER TABLE turn_contexts ADD COLUMN route_decision TEXT NOT NULL DEFAULT 'pending'")
            mcp_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()
            }
            if "last_error" not in mcp_columns and mcp_columns:
                conn.execute("ALTER TABLE mcp_servers ADD COLUMN last_error TEXT")
            if "discovered_tools" not in mcp_columns and mcp_columns:
                conn.execute("ALTER TABLE mcp_servers ADD COLUMN discovered_tools TEXT NOT NULL DEFAULT '[]'")

    def create_conversation(self, title: str | None = None) -> tuple[str, datetime]:
        conversation_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title or "New Conversation", now, now),
            )
        return conversation_id, _utc_from_iso(now)

    def ensure_conversation(self, conversation_id: str) -> bool:
        with self._conn as conn:
            row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return row is not None

    def maybe_set_title_from_message(self, conversation_id: str, user_message: str) -> None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM interaction_events WHERE conversation_id = ? AND role = 'user'",
                (conversation_id,),
            ).fetchone()
            if row is None or int(row["count"]) > 1:
                return
            title = user_message.strip().replace("\n", " ")[:48] or "New Conversation"
            conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        cleaned = title.strip()
        if not cleaned:
            return
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned[:96], now, conversation_id),
            )

    def create_interaction_event(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str,
        event_type: str = "message",
        causation_event_id: str | None = None,
        error: str | None = None,
    ) -> StoredInteractionEvent:
        event_id = str(uuid.uuid4())
        now = _utc_now_iso()
        processed_at = now if status in {"completed", "failed"} else None
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO interaction_events(
                    id, conversation_id, role, event_type, content, status,
                    created_at, processed_at, error, causation_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    role,
                    event_type,
                    content,
                    status,
                    now,
                    processed_at,
                    error,
                    causation_event_id,
                ),
            )
            if role in ("user", "assistant"):
                conn.execute(
                    """
                    UPDATE conversations
                    SET updated_at = ?, message_count = message_count + 1,
                        last_message_preview = ?
                    WHERE id = ?
                    """,
                    (now, content.strip()[:160], conversation_id),
                )
            else:
                conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        return StoredInteractionEvent(
            id=event_id,
            conversation_id=conversation_id,
            role=role,
            event_type=event_type,
            content=content,
            status=status,
            created_at=_utc_from_iso(now),
            processed_at=_utc_from_iso(processed_at) if processed_at else None,
            error=error,
            causation_event_id=causation_event_id,
        )

    def get_conversation_events(self, conversation_id: str) -> list[StoredInteractionEvent]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, event_type, content, status,
                       created_at, processed_at, error, causation_event_id
                FROM interaction_events
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            StoredInteractionEvent(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                event_type=row["event_type"],
                content=row["content"],
                status=row["status"],
                created_at=_utc_from_iso(row["created_at"]),
                processed_at=_utc_from_iso(row["processed_at"]) if row["processed_at"] else None,
                error=row["error"],
                causation_event_id=row["causation_event_id"],
            )
            for row in rows
        ]

    def get_messages(self, conversation_id: str) -> list[StoredMessage]:
        events = self.get_conversation_events(conversation_id)
        result: list[StoredMessage] = []
        for event in events:
            if event.role not in {"user", "assistant"}:
                continue
            result.append(
                StoredMessage(
                    id=event.id,
                    role=event.role,
                    content=event.content,
                    created_at=event.created_at,
                )
            )
        return result

    def create_orchestration_event(
        self,
        *,
        event_type: str,
        label: str,
        status: str,
        conversation_id: str | None = None,
        parent_event_id: str | None = None,
        document_id: str | None = None,
        detail: str | None = None,
        payload: dict | None = None,
        error: str | None = None,
    ) -> StoredOrchestrationEvent:
        event_id = str(uuid.uuid4())
        now = _utc_now_iso()
        processed_at = now if status in {"completed", "failed"} else None
        payload_json = json.dumps(payload or {})
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO orchestration_events(
                    id, conversation_id, parent_event_id, document_id, event_type, label, detail,
                    status, payload, created_at, processed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    parent_event_id,
                    document_id,
                    event_type,
                    label,
                    detail,
                    status,
                    payload_json,
                    now,
                    processed_at,
                    error,
                ),
            )
            if conversation_id:
                conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        return StoredOrchestrationEvent(
            id=event_id,
            conversation_id=conversation_id,
            parent_event_id=parent_event_id,
            document_id=document_id,
            event_type=event_type,
            label=label,
            detail=detail,
            status=status,
            payload=payload or {},
            created_at=_utc_from_iso(now),
            processed_at=_utc_from_iso(processed_at) if processed_at else None,
            error=error,
        )

    def list_conversation_orchestration_events(self, conversation_id: str, limit: int = 100) -> list[StoredOrchestrationEvent]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, parent_event_id, document_id, event_type, label, detail,
                       status, payload, created_at, processed_at, error
                FROM orchestration_events
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            StoredOrchestrationEvent(
                id=row["id"],
                conversation_id=row["conversation_id"],
                parent_event_id=row["parent_event_id"],
                document_id=row["document_id"],
                event_type=row["event_type"],
                label=row["label"],
                detail=row["detail"],
                status=row["status"],
                payload=json.loads(row["payload"] or "{}"),
                created_at=_utc_from_iso(row["created_at"]),
                processed_at=_utc_from_iso(row["processed_at"]) if row["processed_at"] else None,
                error=row["error"],
            )
            for row in rows
        ]

    def claim_next_pending_orchestration_event(self, event_type: str | None = None) -> StoredOrchestrationEvent | None:
        with self._conn as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_type:
                row = conn.execute(
                    """
                    SELECT id, conversation_id, parent_event_id, document_id, event_type, label, detail,
                           status, payload, created_at, processed_at, error
                    FROM orchestration_events
                    WHERE status = 'pending' AND event_type = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (event_type,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, conversation_id, parent_event_id, document_id, event_type, label, detail,
                           status, payload, created_at, processed_at, error
                    FROM orchestration_events
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE orchestration_events SET status = 'processing', processed_at = NULL, error = NULL WHERE id = ?",
                (row["id"],),
            )
            conn.execute("COMMIT")
        return StoredOrchestrationEvent(
            id=row["id"],
            conversation_id=row["conversation_id"],
            parent_event_id=row["parent_event_id"],
            document_id=row["document_id"],
            event_type=row["event_type"],
            label=row["label"],
            detail=row["detail"],
            status="processing",
            payload=json.loads(row["payload"] or "{}"),
            created_at=_utc_from_iso(row["created_at"]),
            processed_at=None,
            error=None,
        )

    def update_orchestration_event(
        self,
        event_id: str,
        *,
        status: str | None = None,
        label: str | None = None,
        detail: str | None = None,
        payload: dict | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn as conn:
            existing = conn.execute(
                "SELECT payload FROM orchestration_events WHERE id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
            if existing is None:
                return
            next_payload = payload if payload is not None else json.loads(existing["payload"] or "{}")
            next_status = status or conn.execute(
                "SELECT status FROM orchestration_events WHERE id = ? LIMIT 1",
                (event_id,),
            ).fetchone()["status"]
            processed_at = _utc_now_iso() if next_status in {"completed", "failed"} else None
            conn.execute(
                """
                UPDATE orchestration_events
                SET status = COALESCE(?, status),
                    label = COALESCE(?, label),
                    detail = COALESCE(?, detail),
                    payload = ?,
                    processed_at = ?,
                    error = ?
                WHERE id = ?
                """,
                (
                    status,
                    label,
                    detail,
                    json.dumps(next_payload),
                    processed_at,
                    error,
                    event_id,
                ),
            )

    def upsert_turn_context(
        self,
        *,
        user_event_id: str,
        conversation_id: str,
        route_decision: str,
        retrieved_chunks: list[StoredRetrievedChunk],
        tool_observations: list[dict],
        memory_candidates: list[dict],
    ) -> StoredTurnContext:
        now = _utc_now_iso()
        retrieved_payload = json.dumps(
            [
                {
                    "content": chunk.content,
                    "score": chunk.score,
                    "source_id": chunk.source_id,
                    "source_type": chunk.source_type,
                    "source_preview": chunk.source_preview,
                }
                for chunk in retrieved_chunks
            ]
        )
        with self._conn as conn:
            row = conn.execute("SELECT user_event_id, created_at FROM turn_contexts WHERE user_event_id = ?", (user_event_id,)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO turn_contexts(
                        user_event_id, conversation_id, route_decision, retrieved_chunks, tool_observations, memory_candidates, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_event_id,
                        conversation_id,
                        route_decision,
                        retrieved_payload,
                        json.dumps(tool_observations),
                        json.dumps(memory_candidates),
                        now,
                        now,
                    ),
                )
                created_at = now
            else:
                conn.execute(
                    """
                    UPDATE turn_contexts
                    SET conversation_id = ?, route_decision = ?, retrieved_chunks = ?, tool_observations = ?, memory_candidates = ?, updated_at = ?
                    WHERE user_event_id = ?
                    """,
                    (
                        conversation_id,
                        route_decision,
                        retrieved_payload,
                        json.dumps(tool_observations),
                        json.dumps(memory_candidates),
                        now,
                        user_event_id,
                    ),
                )
                created_at = row["created_at"]
        return StoredTurnContext(
            user_event_id=user_event_id,
            conversation_id=conversation_id,
            route_decision=route_decision,
            retrieved_chunks=retrieved_chunks,
            tool_observations=tool_observations,
            memory_candidates=memory_candidates,
            created_at=_utc_from_iso(created_at),
            updated_at=_utc_from_iso(now),
        )

    def get_turn_context(self, user_event_id: str) -> StoredTurnContext | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT user_event_id, conversation_id, route_decision, retrieved_chunks, tool_observations, memory_candidates, created_at, updated_at
                FROM turn_contexts
                WHERE user_event_id = ?
                LIMIT 1
                """,
                (user_event_id,),
            ).fetchone()
        if row is None:
            return None
        retrieved_chunks = [
            StoredRetrievedChunk(
                content=str(item.get("content", "")),
                score=float(item.get("score", 0.0)),
                source_id=str(item.get("source_id", "")),
                source_type=str(item.get("source_type", "")),
                source_preview=str(item.get("source_preview", "")),
            )
            for item in json.loads(row["retrieved_chunks"] or "[]")
            if isinstance(item, dict)
        ]
        return StoredTurnContext(
            user_event_id=row["user_event_id"],
            conversation_id=row["conversation_id"],
            route_decision=row["route_decision"] or "pending",
            retrieved_chunks=retrieved_chunks,
            tool_observations=list(json.loads(row["tool_observations"] or "[]")),
            memory_candidates=list(json.loads(row["memory_candidates"] or "[]")),
            created_at=_utc_from_iso(row["created_at"]),
            updated_at=_utc_from_iso(row["updated_at"]),
        )

    def create_document_import(
        self,
        *,
        filename: str,
        media_type: str,
        stored_path: str,
        conversation_id: str | None = None,
        file_hash: str | None = None,
    ) -> StoredDocumentImport:
        doc_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO document_imports(id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (doc_id, conversation_id, filename, media_type, file_hash, stored_path, now),
            )
        return StoredDocumentImport(
            id=doc_id,
            conversation_id=conversation_id,
            filename=filename,
            media_type=media_type,
            file_hash=file_hash,
            stored_path=stored_path,
            status="pending",
            created_at=_utc_from_iso(now),
            processed_at=None,
            error=None,
        )

    def find_document_import_by_hash(self, *, file_hash: str, conversation_id: str | None = None) -> StoredDocumentImport | None:
        with self._conn as conn:
            if conversation_id is not None:
                row = conn.execute(
                    """
                    SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                    FROM document_imports
                    WHERE file_hash = ? AND conversation_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (file_hash, conversation_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                    FROM document_imports
                    WHERE file_hash = ? AND conversation_id IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (file_hash,),
                ).fetchone()
        return self._row_to_document_import(row)

    def get_document_import(self, document_id: str) -> StoredDocumentImport | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                FROM document_imports
                WHERE id = ?
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        return self._row_to_document_import(row)

    def update_document_import_status(self, document_id: str, *, status: str, error: str | None = None) -> None:
        processed_at = _utc_now_iso() if status in {"completed", "failed"} else None
        with self._conn as conn:
            conn.execute(
                """
                UPDATE document_imports
                SET status = ?, processed_at = ?, error = ?
                WHERE id = ?
                """,
                (status, processed_at, error, document_id),
            )

    def list_document_imports(self, limit: int = 200) -> list[StoredDocumentImport]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                FROM document_imports
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [item for row in rows if (item := self._row_to_document_import(row)) is not None]

    def list_recent_completed_document_imports(self, conversation_id: str, limit: int = 3) -> list[StoredDocumentImport]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                FROM document_imports
                WHERE conversation_id = ? AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [item for row in rows if (item := self._row_to_document_import(row)) is not None]

    def list_recent_document_imports_for_conversation(self, conversation_id: str, limit: int = 3) -> list[StoredDocumentImport]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, filename, media_type, file_hash, stored_path, status, created_at, processed_at, error
                FROM document_imports
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [item for row in rows if (item := self._row_to_document_import(row)) is not None]

    def list_rag_chunks_for_source(self, source_type: str, source_id: str, limit: int = 3) -> list[StoredRagChunk]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, source_type, source_id, content, embedding, created_at
                FROM rag_chunks
                WHERE source_type = ? AND source_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (source_type, source_id, limit),
            ).fetchall()
        return [
            StoredRagChunk(
                id=row["id"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                content=row["content"],
                created_at=_utc_from_iso(row["created_at"]),
                embedding=[float(v) for v in json.loads(row["embedding"])],
            )
            for row in rows
        ]

    def delete_rag_chunks_for_source(self, source_type: str, source_id: str) -> int:
        with self._conn as conn:
            cursor = conn.execute(
                "DELETE FROM rag_chunks WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            )
        return int(cursor.rowcount or 0)

    def delete_document_import(self, document_id: str) -> StoredDocumentImport | None:
        document = self.get_document_import(document_id)
        if document is None:
            return None
        with self._conn as conn:
            conn.execute("DELETE FROM rag_chunks WHERE source_type = 'document_import' AND source_id = ?", (document_id,))
            conn.execute("DELETE FROM orchestration_events WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM document_imports WHERE id = ?", (document_id,))
        return document

    def _row_to_document_import(self, row: sqlite3.Row | None) -> StoredDocumentImport | None:
        if row is None:
            return None
        return StoredDocumentImport(
            id=row["id"],
            conversation_id=row["conversation_id"],
            filename=row["filename"],
            media_type=row["media_type"],
            file_hash=row["file_hash"],
            stored_path=row["stored_path"],
            status=row["status"],
            created_at=_utc_from_iso(row["created_at"]),
            processed_at=_utc_from_iso(row["processed_at"]) if row["processed_at"] else None,
            error=row["error"],
        )

    def create_mcp_server(
        self,
        *,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> StoredMcpServer:
        server_id = str(uuid.uuid4())
        now = _utc_now_iso()
        status = "configured" if enabled else "disabled"
        args_payload = json.dumps(args or [])
        env_payload = json.dumps(env or {})
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO mcp_servers(
                    id, name, transport, command, args, url, env, enabled, status, discovered_tools, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)
                """,
                (server_id, name, transport, command, args_payload, url, env_payload, 1 if enabled else 0, status, now, now),
            )
        return StoredMcpServer(
            id=server_id,
            name=name,
            transport=transport,
            command=command,
            args=list(args or []),
            url=url,
            env=dict(env or {}),
            enabled=enabled,
            status=status,
            last_error=None,
            discovered_tools=[],
            created_at=_utc_from_iso(now),
            updated_at=_utc_from_iso(now),
        )

    def list_mcp_servers(self) -> list[StoredMcpServer]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, name, transport, command, args, url, env, enabled, status, last_error, discovered_tools, created_at, updated_at
                FROM mcp_servers
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [item for row in rows if (item := self._row_to_mcp_server(row)) is not None]

    def get_mcp_server(self, server_id: str) -> StoredMcpServer | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, name, transport, command, args, url, env, enabled, status, last_error, discovered_tools, created_at, updated_at
                FROM mcp_servers
                WHERE id = ?
                LIMIT 1
                """,
                (server_id,),
            ).fetchone()
        return self._row_to_mcp_server(row)

    def find_mcp_server_by_name(self, name: str) -> StoredMcpServer | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, name, transport, command, args, url, env, enabled, status, last_error, discovered_tools, created_at, updated_at
                FROM mcp_servers
                WHERE name = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
        return self._row_to_mcp_server(row)

    def update_mcp_server(
        self,
        server_id: str,
        *,
        name: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        enabled: bool | None = None,
    ) -> StoredMcpServer | None:
        current = self.get_mcp_server(server_id)
        if current is None:
            return None
        next_name = name if name is not None else current.name
        next_command = command if command is not None else current.command
        next_args = args if args is not None else current.args
        next_url = url if url is not None else current.url
        next_env = env if env is not None else current.env
        next_enabled = enabled if enabled is not None else current.enabled
        next_status = "configured" if next_enabled else "disabled"
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE mcp_servers
                SET name = ?, command = ?, args = ?, url = ?, env = ?, enabled = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    next_command,
                    json.dumps(next_args),
                    next_url,
                    json.dumps(next_env),
                    1 if next_enabled else 0,
                    next_status,
                    now,
                    server_id,
                ),
            )
        return self.get_mcp_server(server_id)

    def refresh_mcp_server(self, server_id: str) -> StoredMcpServer | None:
        current = self.get_mcp_server(server_id)
        if current is None:
            return None
        now = _utc_now_iso()
        status = "configured" if current.enabled else "disabled"
        with self._conn as conn:
            conn.execute(
                """
                UPDATE mcp_servers
                SET status = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, now, server_id),
            )
        return self.get_mcp_server(server_id)

    def set_mcp_server_discovery_result(
        self,
        server_id: str,
        *,
        discovered_tools: list[dict],
        status: str = "connected",
        last_error: str | None = None,
    ) -> StoredMcpServer | None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE mcp_servers
                SET discovered_tools = ?, status = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(discovered_tools), status, last_error, now, server_id),
            )
        return self.get_mcp_server(server_id)

    def set_mcp_server_error(self, server_id: str, *, error: str, status: str = "error") -> StoredMcpServer | None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE mcp_servers
                SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, now, server_id),
            )
        return self.get_mcp_server(server_id)

    def delete_mcp_server(self, server_id: str) -> bool:
        with self._conn as conn:
            cursor = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        return bool(cursor.rowcount)

    def _row_to_mcp_server(self, row: sqlite3.Row | None) -> StoredMcpServer | None:
        if row is None:
            return None
        return StoredMcpServer(
            id=row["id"],
            name=row["name"],
            transport=row["transport"],
            command=row["command"],
            args=list(json.loads(row["args"] or "[]")),
            url=row["url"],
            env=dict(json.loads(row["env"] or "{}")),
            enabled=bool(row["enabled"]),
            status=row["status"],
            last_error=row["last_error"],
            discovered_tools=list(json.loads(row["discovered_tools"] or "[]")),
            created_at=_utc_from_iso(row["created_at"]),
            updated_at=_utc_from_iso(row["updated_at"]),
        )

    def get_interaction_event(self, event_id: str) -> StoredInteractionEvent | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, role, event_type, content, status,
                       created_at, processed_at, error, causation_event_id
                FROM interaction_events
                WHERE id = ?
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredInteractionEvent(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            event_type=row["event_type"],
            content=row["content"],
            status=row["status"],
            created_at=_utc_from_iso(row["created_at"]),
            processed_at=_utc_from_iso(row["processed_at"]) if row["processed_at"] else None,
            error=row["error"],
            causation_event_id=row["causation_event_id"],
        )

    def get_conversation_detail(self, conversation_id: str) -> tuple[str, datetime, list[StoredMessage]] | None:
        with self._conn as conn:
            convo = conn.execute(
                "SELECT title, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if convo is None:
            return None
        return (
            convo["title"],
            _utc_from_iso(convo["updated_at"]),
            self.get_messages(conversation_id),
        )

    def list_conversations(self) -> list[StoredConversation]:
        conn = self._conn
        rows = conn.execute(
            """
            SELECT id, title, updated_at, last_message_preview, message_count
            FROM conversations
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [
            StoredConversation(
                id=row["id"],
                title=row["title"],
                updated_at=_utc_from_iso(row["updated_at"]),
                last_message=row["last_message_preview"],
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]

    def delete_conversation(self, conversation_id: str) -> bool:
        conn = self._conn
        row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return False
        # rag_chunks aren't FK-linked to conversations, so delete manually.
        conn.execute(
            """
            DELETE FROM rag_chunks
            WHERE source_id IN (
                SELECT id FROM interaction_events WHERE conversation_id = ?
            )
            """,
            (conversation_id,),
        )
        # interaction_events and performance_exchanges cascade from conversations.
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def add_message(self, conversation_id: str, role: str, content: str) -> StoredMessage:
        event = self.create_interaction_event(conversation_id, role, content, status="completed")
        return StoredMessage(id=event.id, role=event.role, content=event.content, created_at=event.created_at)

    def claim_next_pending_user_event(self) -> StoredInteractionEvent | None:
        with self._conn as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, conversation_id, role, event_type, content, status,
                       created_at, processed_at, error, causation_event_id
                FROM interaction_events
                WHERE status = 'pending' AND role = 'user'
                  AND EXISTS (
                    SELECT 1
                    FROM turn_contexts
                    WHERE turn_contexts.user_event_id = interaction_events.id
                      AND turn_contexts.route_decision = 'direct_dialogue'
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            processed_at = _utc_now_iso()
            conn.execute(
                "UPDATE interaction_events SET status = 'processing', processed_at = NULL, error = NULL WHERE id = ?",
                (row["id"],),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (processed_at, row["conversation_id"]))
            conn.execute("COMMIT")
        return StoredInteractionEvent(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            event_type=row["event_type"],
            content=row["content"],
            status="processing",
            created_at=_utc_from_iso(row["created_at"]),
            processed_at=None,
            error=None,
            causation_event_id=row["causation_event_id"],
        )

    def mark_event_processing(self, event_id: str) -> None:
        with self._conn as conn:
            conn.execute(
                "UPDATE interaction_events SET status = 'processing', processed_at = NULL, error = NULL WHERE id = ?",
                (event_id,),
            )

    def mark_event_completed(self, event_id: str) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                "UPDATE interaction_events SET status = 'completed', processed_at = ?, error = NULL WHERE id = ?",
                (now, event_id),
            )

    def mark_event_completed_with_content(self, event_id: str, content: str) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE interaction_events
                SET content = ?, status = 'completed', processed_at = ?, error = NULL
                WHERE id = ?
                """,
                (content, now, event_id),
            )

    def mark_event_failed(self, event_id: str, error: str) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                "UPDATE interaction_events SET status = 'failed', processed_at = ?, error = ? WHERE id = ?",
                (now, error[:1000], event_id),
            )

    def mark_event_failed_with_content(self, event_id: str, content: str, error: str) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE interaction_events
                SET content = ?, status = 'failed', processed_at = ?, error = ?
                WHERE id = ?
                """,
                (content, now, error[:1000], event_id),
            )

    def update_interaction_event_content(self, event_id: str, content: str) -> None:
        now = _utc_now_iso()
        conn = self._conn
        conn.execute(
            "UPDATE interaction_events SET content = ?, processed_at = ?, status = 'processing' WHERE id = ?",
            (content, now, event_id),
        )
        row = conn.execute(
            "SELECT conversation_id, role FROM interaction_events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE conversations SET updated_at = ?, last_message_preview = ? WHERE id = ?",
                (now, content.strip()[:160], row["conversation_id"]),
            )

    def upsert_rag_chunks(self, source_type: str, source_id: str, chunks: list[tuple[str, list[float]]]) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute("DELETE FROM rag_chunks WHERE source_type = ? AND source_id = ?", (source_type, source_id))
            for content, embedding in chunks:
                conn.execute(
                    """
                    INSERT INTO rag_chunks(id, source_type, source_id, content, embedding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), source_type, source_id, content, json.dumps(embedding), now),
                )

    def list_rag_chunks(self, limit: int = 500, source_types: list[str] | None = None) -> list[StoredRagChunk]:
        query = """
                SELECT id, source_type, source_id, content, embedding, created_at
                FROM rag_chunks
                """
        params: list[object] = []
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            query += f" WHERE source_type IN ({placeholders})"
            params.extend(source_types)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            StoredRagChunk(
                id=row["id"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                content=row["content"],
                created_at=_utc_from_iso(row["created_at"]),
                embedding=[float(v) for v in json.loads(row["embedding"])],
            )
            for row in rows
        ]

    def iter_rag_chunks(self, source_types: list[str] | None = None) -> list[StoredRagChunk]:
        return self.list_rag_chunks(limit=5000, source_types=source_types)

    def count_rag_chunks(self, source_types: list[str] | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM rag_chunks"
        params: list[object] = []
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            query += f" WHERE source_type IN ({placeholders})"
            params.extend(source_types)
        with self._conn as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return int(row["count"] if row is not None else 0)

    def list_oldest_rag_chunks(self, limit: int = 50, source_types: list[str] | None = None) -> list[StoredRagChunk]:
        query = """
                SELECT id, source_type, source_id, content, embedding, created_at
                FROM rag_chunks
                """
        params: list[object] = []
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            query += f" WHERE source_type IN ({placeholders})"
            params.extend(source_types)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._conn as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            StoredRagChunk(
                id=row["id"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                content=row["content"],
                created_at=_utc_from_iso(row["created_at"]),
                embedding=[float(v) for v in json.loads(row["embedding"])],
            )
            for row in rows
        ]

    def delete_rag_chunk(self, chunk_id: str) -> bool:
        with self._conn as conn:
            row = conn.execute("SELECT id FROM rag_chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM rag_chunks WHERE id = ?", (chunk_id,))
        return True

    def delete_rag_chunks(self, chunk_ids: list[str]) -> int:
        unique_ids = [chunk_id for chunk_id in dict.fromkeys(chunk_ids) if chunk_id]
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with self._conn as conn:
            cursor = conn.execute(
                f"DELETE FROM rag_chunks WHERE id IN ({placeholders})",
                tuple(unique_ids),
            )
        return int(cursor.rowcount or 0)

    def add_performance_exchange(
        self,
        conversation_id: str,
        user_event_id: str | None,
        assistant_event_id: str | None,
        user_preview: str,
        assistant_preview: str,
        total_latency_ms: int,
        llm_latency_ms: int,
        ttft_ms: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        response_source: str | None,
        response_policy: str | None,
        llm_involved: bool,
        tool_observations: list[dict],
        workflow_trace: list[dict],
        retrieved_chunks: list[StoredRetrievedChunk],
        system_chars: int,
        user_chars: int,
        assistant_chars: int,
        system_tokens_est: int | None,
        user_tokens_est: int | None,
        assistant_tokens_est: int | None,
    ) -> None:
        exchange_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO performance_exchanges(
                    id, conversation_id, user_event_id, assistant_event_id, created_at, user_preview, assistant_preview,
                    total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
                    response_source, response_policy, llm_involved, tool_observations, workflow_trace,
                    retrieved_chunk_count, retrieved_chunks,
                    system_chars, user_chars, assistant_chars, system_tokens_est, user_tokens_est, assistant_tokens_est
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange_id,
                    conversation_id,
                    user_event_id,
                    assistant_event_id,
                    now,
                    user_preview,
                    assistant_preview,
                    total_latency_ms,
                    llm_latency_ms,
                    ttft_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    response_source,
                    response_policy,
                    1 if llm_involved else 0,
                    json.dumps(tool_observations),
                    json.dumps(workflow_trace),
                    len(retrieved_chunks),
                    json.dumps(
                        [
                            {
                                "content": chunk.content,
                                "score": chunk.score,
                                "source_id": chunk.source_id,
                                "source_type": chunk.source_type,
                                "source_preview": chunk.source_preview,
                            }
                            for chunk in retrieved_chunks
                        ]
                    ),
                    system_chars,
                    user_chars,
                    assistant_chars,
                    system_tokens_est,
                    user_tokens_est,
                    assistant_tokens_est,
                ),
            )

    def list_recent_performance_exchanges(self, limit: int = 5) -> list[StoredPerformanceExchange]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, created_at, user_preview, assistant_preview,
                       user_event_id, assistant_event_id,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
                       response_source, response_policy, llm_involved, tool_observations, workflow_trace,
                       retrieved_chunk_count, retrieved_chunks,
                       system_chars, user_chars, assistant_chars, system_tokens_est, user_tokens_est, assistant_tokens_est
                FROM performance_exchanges
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            StoredPerformanceExchange(
                id=row["id"],
                conversation_id=row["conversation_id"],
                user_event_id=row["user_event_id"],
                assistant_event_id=row["assistant_event_id"],
                created_at=_utc_from_iso(row["created_at"]),
                user_preview=row["user_preview"],
                assistant_preview=row["assistant_preview"],
                total_latency_ms=int(row["total_latency_ms"]),
                llm_latency_ms=int(row["llm_latency_ms"]),
                ttft_ms=row["ttft_ms"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                response_source=row["response_source"],
                response_policy=row["response_policy"],
                llm_involved=bool(row["llm_involved"]),
                tool_observations=list(json.loads(row["tool_observations"] or "[]")),
                workflow_trace=list(json.loads(row["workflow_trace"] or "[]")),
                retrieved_chunk_count=int(row["retrieved_chunk_count"] or 0),
                retrieved_chunks=[
                    StoredRetrievedChunk(
                        content=str(item.get("content", "")),
                        score=float(item.get("score", 0.0)),
                        source_id=str(item.get("source_id", "")),
                        source_type=str(item.get("source_type", "")),
                        source_preview=str(item.get("source_preview", "")),
                    )
                    for item in json.loads(row["retrieved_chunks"] or "[]")
                    if isinstance(item, dict)
                ],
                system_chars=int(row["system_chars"]),
                user_chars=int(row["user_chars"]),
                assistant_chars=int(row["assistant_chars"]),
                system_tokens_est=row["system_tokens_est"],
                user_tokens_est=row["user_tokens_est"],
                assistant_tokens_est=row["assistant_tokens_est"],
            )
            for row in rows
        ]

    def get_latest_performance_exchange_for_conversation(self, conversation_id: str) -> StoredPerformanceExchange | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, created_at, user_preview, assistant_preview,
                       user_event_id, assistant_event_id,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
                       response_source, response_policy, llm_involved, tool_observations, workflow_trace,
                       retrieved_chunk_count, retrieved_chunks,
                       system_chars, user_chars, assistant_chars, system_tokens_est, user_tokens_est, assistant_tokens_est
                FROM performance_exchanges
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredPerformanceExchange(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_event_id=row["user_event_id"],
            assistant_event_id=row["assistant_event_id"],
            created_at=_utc_from_iso(row["created_at"]),
            user_preview=row["user_preview"],
            assistant_preview=row["assistant_preview"],
            total_latency_ms=int(row["total_latency_ms"]),
            llm_latency_ms=int(row["llm_latency_ms"]),
            ttft_ms=row["ttft_ms"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            response_source=row["response_source"],
            response_policy=row["response_policy"],
            llm_involved=bool(row["llm_involved"]),
            tool_observations=list(json.loads(row["tool_observations"] or "[]")),
            workflow_trace=list(json.loads(row["workflow_trace"] or "[]")),
            retrieved_chunk_count=int(row["retrieved_chunk_count"] or 0),
            retrieved_chunks=[
                StoredRetrievedChunk(
                    content=str(item.get("content", "")),
                    score=float(item.get("score", 0.0)),
                    source_id=str(item.get("source_id", "")),
                    source_type=str(item.get("source_type", "")),
                    source_preview=str(item.get("source_preview", "")),
                )
                for item in json.loads(row["retrieved_chunks"] or "[]")
                if isinstance(item, dict)
            ],
            system_chars=int(row["system_chars"]),
            user_chars=int(row["user_chars"]),
            assistant_chars=int(row["assistant_chars"]),
            system_tokens_est=row["system_tokens_est"],
            user_tokens_est=row["user_tokens_est"],
            assistant_tokens_est=row["assistant_tokens_est"],
        )

    def get_performance_exchange_for_user_event(self, user_event_id: str) -> StoredPerformanceExchange | None:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, created_at, user_preview, assistant_preview,
                       user_event_id, assistant_event_id,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
                       response_source, response_policy, llm_involved, tool_observations, workflow_trace,
                       retrieved_chunk_count, retrieved_chunks,
                       system_chars, user_chars, assistant_chars, system_tokens_est, user_tokens_est, assistant_tokens_est
                FROM performance_exchanges
                WHERE user_event_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_event_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredPerformanceExchange(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_event_id=row["user_event_id"],
            assistant_event_id=row["assistant_event_id"],
            created_at=_utc_from_iso(row["created_at"]),
            user_preview=row["user_preview"],
            assistant_preview=row["assistant_preview"],
            total_latency_ms=int(row["total_latency_ms"]),
            llm_latency_ms=int(row["llm_latency_ms"]),
            ttft_ms=row["ttft_ms"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            response_source=row["response_source"],
            response_policy=row["response_policy"],
            llm_involved=bool(row["llm_involved"]),
            tool_observations=list(json.loads(row["tool_observations"] or "[]")),
            workflow_trace=list(json.loads(row["workflow_trace"] or "[]")),
            retrieved_chunk_count=int(row["retrieved_chunk_count"] or 0),
            retrieved_chunks=[
                StoredRetrievedChunk(
                    content=str(item.get("content", "")),
                    score=float(item.get("score", 0.0)),
                    source_id=str(item.get("source_id", "")),
                    source_type=str(item.get("source_type", "")),
                    source_preview=str(item.get("source_preview", "")),
                )
                for item in json.loads(row["retrieved_chunks"] or "[]")
                if isinstance(item, dict)
            ],
            system_chars=int(row["system_chars"]),
            user_chars=int(row["user_chars"]),
            assistant_chars=int(row["assistant_chars"]),
            system_tokens_est=row["system_tokens_est"],
            user_tokens_est=row["user_tokens_est"],
            assistant_tokens_est=row["assistant_tokens_est"],
        )

    def _aggregate_tokens(self, since_iso: str | None = None) -> tuple[int, int, int, int]:
        query = """
            SELECT
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(COUNT(*), 0) AS exchange_count
            FROM performance_exchanges
        """
        params: tuple = ()
        if since_iso is not None:
            query += " WHERE created_at >= ?"
            params = (since_iso,)
        with self._conn as conn:
            row = conn.execute(query, params).fetchone()
        return (
            int(row["total_tokens"]),
            int(row["prompt_tokens"]),
            int(row["completion_tokens"]),
            int(row["exchange_count"]),
        )

    def summarize_performance(self) -> dict:
        with self._conn as conn:
            latency = conn.execute(
                """
                SELECT
                    COALESCE(MIN(total_latency_ms), 0) AS min_latency,
                    COALESCE(MAX(total_latency_ms), 0) AS max_latency,
                    COALESCE(AVG(total_latency_ms), 0) AS avg_latency,
                    COALESCE(COUNT(*), 0) AS exchange_count
                FROM performance_exchanges
                """
            ).fetchone()
        now = datetime.now(timezone.utc)
        day_since = (now.replace(microsecond=0) - timedelta(days=1)).isoformat()
        week_since = (now.replace(microsecond=0) - timedelta(days=7)).isoformat()
        month_since = (now.replace(microsecond=0) - timedelta(days=30)).isoformat()
        return {
            "latency_min_ms": int(latency["min_latency"]),
            "latency_max_ms": int(latency["max_latency"]),
            "latency_avg_ms": float(latency["avg_latency"] or 0),
            "exchange_count": int(latency["exchange_count"]),
            "tokens_day": self._aggregate_tokens(day_since),
            "tokens_week": self._aggregate_tokens(week_since),
            "tokens_month": self._aggregate_tokens(month_since),
            "tokens_all_time": self._aggregate_tokens(None),
        }

    def ensure_default_prompt_profile(self, tenant_id: str) -> StoredPromptProfile:
        with self._conn as conn:
            existing = conn.execute(
                """
                SELECT id, tenant_id, name, is_default, is_active, updated_at
                FROM prompt_profiles
                WHERE tenant_id = ? AND is_default = 1
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
            if existing is not None:
                if int(existing["is_active"]) != 1:
                    conn.execute("UPDATE prompt_profiles SET is_active = 0 WHERE tenant_id = ?", (tenant_id,))
                    conn.execute("UPDATE prompt_profiles SET is_active = 1 WHERE id = ?", (existing["id"],))
                return StoredPromptProfile(
                    id=existing["id"],
                    tenant_id=existing["tenant_id"],
                    name=existing["name"],
                    is_default=bool(existing["is_default"]),
                    is_active=True,
                    updated_at=_utc_from_iso(existing["updated_at"]),
                )
            profile_id = str(uuid.uuid4())
            now = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO prompt_profiles(id, tenant_id, name, is_default, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 1, 1, ?, ?)
                """,
                (profile_id, tenant_id, "Default", now, now),
            )
        return StoredPromptProfile(
            id=profile_id,
            tenant_id=tenant_id,
            name="Default",
            is_default=True,
            is_active=True,
            updated_at=_utc_from_iso(now),
        )

    def get_active_prompt_profile(self, tenant_id: str) -> StoredPromptProfile:
        self.ensure_default_prompt_profile(tenant_id)
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, name, is_default, is_active, updated_at
                FROM prompt_profiles
                WHERE tenant_id = ? AND is_active = 1
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"No active prompt profile found for tenant '{tenant_id}'")
        return StoredPromptProfile(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_default=bool(row["is_default"]),
            is_active=bool(row["is_active"]),
            updated_at=_utc_from_iso(row["updated_at"]),
        )

    def list_prompt_profiles(self, tenant_id: str) -> list[StoredPromptProfile]:
        self.ensure_default_prompt_profile(tenant_id)
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, tenant_id, name, is_default, is_active, updated_at
                FROM prompt_profiles
                WHERE tenant_id = ?
                ORDER BY is_active DESC, is_default DESC, name ASC
                """,
                (tenant_id,),
            ).fetchall()
        return [
            StoredPromptProfile(
                id=row["id"],
                tenant_id=row["tenant_id"],
                name=row["name"],
                is_default=bool(row["is_default"]),
                is_active=bool(row["is_active"]),
                updated_at=_utc_from_iso(row["updated_at"]),
            )
            for row in rows
        ]

    def create_prompt_profile(self, tenant_id: str, name: str) -> StoredPromptProfile:
        profile_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                INSERT INTO prompt_profiles(id, tenant_id, name, is_default, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 0, 0, ?, ?)
                """,
                (profile_id, tenant_id, name.strip(), now, now),
            )
        return StoredPromptProfile(
            id=profile_id,
            tenant_id=tenant_id,
            name=name.strip(),
            is_default=False,
            is_active=False,
            updated_at=_utc_from_iso(now),
        )

    def activate_prompt_profile(self, tenant_id: str, profile_id: str) -> bool:
        with self._conn as conn:
            row = conn.execute(
                "SELECT id FROM prompt_profiles WHERE id = ? AND tenant_id = ?",
                (profile_id, tenant_id),
            ).fetchone()
            if row is None:
                return False
            now = _utc_now_iso()
            conn.execute("UPDATE prompt_profiles SET is_active = 0 WHERE tenant_id = ?", (tenant_id,))
            conn.execute("UPDATE prompt_profiles SET is_active = 1, updated_at = ? WHERE id = ?", (now, profile_id))
        return True

    def get_prompt_overrides(self, profile_id: str) -> dict[str, dict]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT component_id, content, enabled
                FROM prompt_component_overrides
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchall()
        overrides: dict[str, dict] = {}
        for row in rows:
            overrides[row["component_id"]] = {
                "content": row["content"],
                "enabled": None if row["enabled"] is None else bool(row["enabled"]),
            }
        return overrides

    def upsert_prompt_override(self, profile_id: str, component_id: str, content: str | None, enabled: bool | None) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            row = conn.execute(
                "SELECT id FROM prompt_component_overrides WHERE profile_id = ? AND component_id = ?",
                (profile_id, component_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO prompt_component_overrides(id, profile_id, component_id, content, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), profile_id, component_id, content, None if enabled is None else int(enabled), now),
                )
            else:
                conn.execute(
                    """
                    UPDATE prompt_component_overrides
                    SET content = COALESCE(?, content),
                        enabled = COALESCE(?, enabled),
                        updated_at = ?
                    WHERE profile_id = ? AND component_id = ?
                    """,
                    (content, None if enabled is None else int(enabled), now, profile_id, component_id),
                )
            conn.execute("UPDATE prompt_profiles SET updated_at = ? WHERE id = ?", (now, profile_id))

    def reset_prompt_profile(self, profile_id: str) -> None:
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute("DELETE FROM prompt_component_overrides WHERE profile_id = ?", (profile_id,))
            conn.execute("UPDATE prompt_profiles SET updated_at = ? WHERE id = ?", (now, profile_id))

    def delete_all_data(self) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM performance_exchanges")
            conn.execute("DELETE FROM turn_contexts")
            conn.execute("DELETE FROM orchestration_events")
            conn.execute("DELETE FROM document_imports")
            conn.execute("DELETE FROM mcp_servers")
            conn.execute("DELETE FROM rag_chunks")
            conn.execute("DELETE FROM interaction_events")
            conn.execute("DELETE FROM conversations")
            conn.execute("DELETE FROM prompt_component_overrides")
            conn.execute("DELETE FROM prompt_profiles")
            conn.execute("DELETE FROM prompt_context_settings")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("REINDEX")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")

    def export_all_data(self, tenant_id: str) -> dict:
        with self._conn as conn:
            conversations = [dict(row) for row in conn.execute("SELECT * FROM conversations ORDER BY created_at ASC").fetchall()]
            events = [dict(row) for row in conn.execute("SELECT * FROM interaction_events ORDER BY created_at ASC").fetchall()]
            orchestration_events = [dict(row) for row in conn.execute("SELECT * FROM orchestration_events ORDER BY created_at ASC").fetchall()]
            turn_contexts = [dict(row) for row in conn.execute("SELECT * FROM turn_contexts ORDER BY created_at ASC").fetchall()]
            document_imports = [dict(row) for row in conn.execute("SELECT * FROM document_imports ORDER BY created_at ASC").fetchall()]
            mcp_servers = [dict(row) for row in conn.execute("SELECT * FROM mcp_servers ORDER BY created_at ASC").fetchall()]
            chunks = [dict(row) for row in conn.execute("SELECT * FROM rag_chunks ORDER BY created_at ASC").fetchall()]
            performance = [dict(row) for row in conn.execute("SELECT * FROM performance_exchanges ORDER BY created_at ASC").fetchall()]
            prompt_profiles = [dict(row) for row in conn.execute("SELECT * FROM prompt_profiles WHERE tenant_id = ? ORDER BY created_at ASC", (tenant_id,)).fetchall()]
            prompt_overrides = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT o.id, o.profile_id, o.component_id, o.content, o.enabled, o.updated_at
                    FROM prompt_component_overrides o
                    INNER JOIN prompt_profiles p ON p.id = o.profile_id
                    WHERE p.tenant_id = ?
                    ORDER BY o.updated_at ASC
                    """,
                    (tenant_id,),
                ).fetchall()
            ]
            context_settings = conn.execute("SELECT * FROM prompt_context_settings WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return {
            "tenant_id": tenant_id,
            "exported_at": _utc_now_iso(),
            "conversations": conversations,
            "interaction_events": events,
            "orchestration_events": orchestration_events,
            "turn_contexts": turn_contexts,
            "document_imports": document_imports,
            "mcp_servers": mcp_servers,
            "rag_chunks": chunks,
            "performance_exchanges": performance,
            "prompt_profiles": prompt_profiles,
            "prompt_component_overrides": prompt_overrides,
            "prompt_context_settings": dict(context_settings) if context_settings is not None else None,
        }

    def ensure_context_settings(
        self,
        tenant_id: str,
        max_context_tokens: int,
        max_response_tokens: int,
        compact_trigger_pct: float,
    ) -> StoredContextSettings:
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, compact_instructions, memory_enabled, updated_at
                FROM prompt_context_settings
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
            if row is None:
                now = _utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO prompt_context_settings(tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, compact_instructions, memory_enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        max_context_tokens,
                        max_response_tokens,
                        compact_trigger_pct,
                        default_compaction_instructions(),
                        1,
                        now,
                    ),
                )
                return StoredContextSettings(
                    tenant_id=tenant_id,
                    max_context_tokens=max_context_tokens,
                    max_response_tokens=max_response_tokens,
                    compact_trigger_pct=compact_trigger_pct,
                    compact_instructions=default_compaction_instructions(),
                    memory_enabled=True,
                    updated_at=_utc_from_iso(now),
                )
            compact_instructions = str(row["compact_instructions"] or "").strip() or default_compaction_instructions()
            return StoredContextSettings(
                tenant_id=row["tenant_id"],
                max_context_tokens=int(row["max_context_tokens"]),
                max_response_tokens=int(row["max_response_tokens"]),
                compact_trigger_pct=float(row["compact_trigger_pct"]),
                compact_instructions=compact_instructions,
                memory_enabled=bool(row["memory_enabled"]),
                updated_at=_utc_from_iso(row["updated_at"]),
            )

    def update_context_settings(
        self,
        tenant_id: str,
        max_context_tokens: int | None = None,
        max_response_tokens: int | None = None,
        compact_trigger_pct: float | None = None,
        compact_instructions: str | None = None,
        memory_enabled: bool | None = None,
    ) -> StoredContextSettings:
        current = self.ensure_context_settings(tenant_id, 4096, 512, 0.9)
        now = _utc_now_iso()
        with self._conn as conn:
            conn.execute(
                """
                UPDATE prompt_context_settings
                SET max_context_tokens = ?,
                    max_response_tokens = ?,
                    compact_trigger_pct = ?,
                    compact_instructions = ?,
                    memory_enabled = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (
                    int(max_context_tokens if max_context_tokens is not None else current.max_context_tokens),
                    int(max_response_tokens if max_response_tokens is not None else current.max_response_tokens),
                    float(compact_trigger_pct if compact_trigger_pct is not None else current.compact_trigger_pct),
                    str(compact_instructions if compact_instructions is not None else current.compact_instructions),
                    int(memory_enabled if memory_enabled is not None else current.memory_enabled),
                    now,
                    tenant_id,
                ),
            )
        return self.ensure_context_settings(tenant_id, 4096, 512, 0.9)

    def update_worker_heartbeat(self, worker_id: str = "dialogue-worker") -> None:
        now = _utc_now_iso()
        conn = self._conn
        conn.execute(
            """
            INSERT INTO worker_heartbeat(worker_id, last_seen) VALUES (?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET last_seen = excluded.last_seen
            """,
            (worker_id, now),
        )

    def get_worker_heartbeat(self, worker_id: str = "dialogue-worker") -> datetime | None:
        conn = self._conn
        row = conn.execute(
            "SELECT last_seen FROM worker_heartbeat WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if row is None:
            return None
        return _utc_from_iso(row["last_seen"])
