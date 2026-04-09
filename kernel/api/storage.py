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
            "conversations",
            ["created_at", "updated_at"],
        )
        deleted += self._delete_rows_with_null_bytes(
            "performance_exchanges",
            ["created_at", "user_event_id", "assistant_event_id"],
        )
        if integrity_status != "ok" or deleted > 0:
            self._conn.execute("REINDEX")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")

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

    def list_rag_chunks(self, limit: int = 500) -> list[StoredRagChunk]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, source_type, source_id, content, embedding, created_at
                FROM rag_chunks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
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

    def iter_rag_chunks(self) -> list[StoredRagChunk]:
        return self.list_rag_chunks(limit=5000)

    def count_rag_chunks(self) -> int:
        with self._conn as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM rag_chunks").fetchone()
        return int(row["count"] if row is not None else 0)

    def list_oldest_rag_chunks(self, limit: int = 50) -> list[StoredRagChunk]:
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT id, source_type, source_id, content, embedding, created_at
                FROM rag_chunks
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
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
                    retrieved_chunk_count, retrieved_chunks,
                    system_chars, user_chars, assistant_chars, system_tokens_est, user_tokens_est, assistant_tokens_est
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                SELECT id, conversation_id, user_event_id, assistant_event_id, created_at, user_preview, assistant_preview,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
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
                SELECT id, conversation_id, user_event_id, assistant_event_id, created_at, user_preview, assistant_preview,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
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
                SELECT id, conversation_id, user_event_id, assistant_event_id, created_at, user_preview, assistant_preview,
                       total_latency_ms, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
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
