from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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
class StoredPerformanceExchange:
    id: str
    conversation_id: str
    created_at: datetime
    user_preview: str
    assistant_preview: str
    total_latency_ms: int
    llm_latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
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
    updated_at: datetime


class ChatStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                );

                CREATE TABLE IF NOT EXISTS performance_exchanges (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    user_preview TEXT NOT NULL,
                    assistant_preview TEXT NOT NULL,
                    total_latency_ms INTEGER NOT NULL,
                    llm_latency_ms INTEGER NOT NULL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    system_chars INTEGER NOT NULL,
                    user_chars INTEGER NOT NULL,
                    assistant_chars INTEGER NOT NULL,
                    system_tokens_est INTEGER,
                    user_tokens_est INTEGER,
                    assistant_tokens_est INTEGER,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
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
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(prompt_context_settings)").fetchall()
            }
            if "max_response_tokens" not in columns:
                conn.execute(
                    "ALTER TABLE prompt_context_settings ADD COLUMN max_response_tokens INTEGER NOT NULL DEFAULT 512"
                )
            conn.commit()

    def create_conversation(self, title: str | None = None) -> tuple[str, datetime]:
        conversation_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title or "New Conversation", now, now),
            )
            conn.commit()
        return conversation_id, _utc_from_iso(now)

    def ensure_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            return row is not None

    def maybe_set_title_from_message(self, conversation_id: str, user_message: str) -> None:
        with self._connect() as conn:
            msg_count = conn.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if msg_count is None or int(msg_count["count"]) > 1:
                return
            title = user_message.strip().replace("\n", " ")[:48] or "New Conversation"
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
            conn.commit()

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        cleaned = title.strip()
        if not cleaned:
            return
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned[:96], now, conversation_id),
            )
            conn.commit()

    def add_message(self, conversation_id: str, role: str, content: str) -> StoredMessage:
        message_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, conversation_id, role, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        return StoredMessage(
            id=message_id,
            role=role,
            content=content,
            created_at=_utc_from_iso(now),
        )

    def get_messages(self, conversation_id: str) -> list[StoredMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            StoredMessage(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=_utc_from_iso(row["created_at"]),
            )
            for row in rows
        ]

    def get_conversation_detail(self, conversation_id: str) -> tuple[str, datetime, list[StoredMessage]] | None:
        with self._connect() as conn:
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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.id AS id,
                    c.title AS title,
                    c.updated_at AS updated_at,
                    COALESCE(last_msg.content, '') AS last_message,
                    COALESCE(msg_count.count, 0) AS message_count
                FROM conversations c
                LEFT JOIN (
                    SELECT m1.conversation_id, m1.content
                    FROM messages m1
                    INNER JOIN (
                        SELECT conversation_id, MAX(created_at) AS max_created_at
                        FROM messages
                        GROUP BY conversation_id
                    ) m2
                    ON m1.conversation_id = m2.conversation_id
                    AND m1.created_at = m2.max_created_at
                ) AS last_msg
                ON c.id = last_msg.conversation_id
                LEFT JOIN (
                    SELECT conversation_id, COUNT(*) AS count
                    FROM messages
                    GROUP BY conversation_id
                ) AS msg_count
                ON c.id = msg_count.conversation_id
                ORDER BY c.updated_at DESC
                """
            ).fetchall()

        return [
            StoredConversation(
                id=row["id"],
                title=row["title"],
                updated_at=_utc_from_iso(row["updated_at"]),
                last_message=row["last_message"],
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if exists is None:
                return False
            conn.execute("DELETE FROM performance_exchanges WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            conn.commit()
        return True

    def add_performance_exchange(
        self,
        conversation_id: str,
        user_preview: str,
        assistant_preview: str,
        total_latency_ms: int,
        llm_latency_ms: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        system_chars: int,
        user_chars: int,
        assistant_chars: int,
        system_tokens_est: int | None,
        user_tokens_est: int | None,
        assistant_tokens_est: int | None,
    ) -> None:
        exchange_id = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO performance_exchanges(
                    id, conversation_id, created_at, user_preview, assistant_preview,
                    total_latency_ms, llm_latency_ms, prompt_tokens, completion_tokens, total_tokens,
                    system_chars, user_chars, assistant_chars,
                    system_tokens_est, user_tokens_est, assistant_tokens_est
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange_id,
                    conversation_id,
                    now,
                    user_preview,
                    assistant_preview,
                    total_latency_ms,
                    llm_latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    system_chars,
                    user_chars,
                    assistant_chars,
                    system_tokens_est,
                    user_tokens_est,
                    assistant_tokens_est,
                ),
            )
            conn.commit()

    def list_recent_performance_exchanges(self, limit: int = 5) -> list[StoredPerformanceExchange]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, conversation_id, created_at, user_preview, assistant_preview,
                    total_latency_ms, llm_latency_ms, prompt_tokens, completion_tokens, total_tokens,
                    system_chars, user_chars, assistant_chars,
                    system_tokens_est, user_tokens_est, assistant_tokens_est
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
                created_at=_utc_from_iso(row["created_at"]),
                user_preview=row["user_preview"],
                assistant_preview=row["assistant_preview"],
                total_latency_ms=int(row["total_latency_ms"]),
                llm_latency_ms=int(row["llm_latency_ms"]),
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                system_chars=int(row["system_chars"]),
                user_chars=int(row["user_chars"]),
                assistant_chars=int(row["assistant_chars"]),
                system_tokens_est=row["system_tokens_est"],
                user_tokens_est=row["user_tokens_est"],
                assistant_tokens_est=row["assistant_tokens_est"],
            )
            for row in rows
        ]

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
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return (
            int(row["total_tokens"]),
            int(row["prompt_tokens"]),
            int(row["completion_tokens"]),
            int(row["exchange_count"]),
        )

    def summarize_performance(self) -> dict:
        with self._connect() as conn:
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
        with self._connect() as conn:
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
                    conn.execute(
                        "UPDATE prompt_profiles SET is_active = 0 WHERE tenant_id = ?",
                        (tenant_id,),
                    )
                    conn.execute(
                        "UPDATE prompt_profiles SET is_active = 1 WHERE id = ?",
                        (existing["id"],),
                    )
                    conn.commit()
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
            conn.commit()
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_profiles(id, tenant_id, name, is_default, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 0, 0, ?, ?)
                """,
                (profile_id, tenant_id, name.strip(), now, now),
            )
            conn.commit()
        return StoredPromptProfile(
            id=profile_id,
            tenant_id=tenant_id,
            name=name.strip(),
            is_default=False,
            is_active=False,
            updated_at=_utc_from_iso(now),
        )

    def activate_prompt_profile(self, tenant_id: str, profile_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM prompt_profiles WHERE id = ? AND tenant_id = ?",
                (profile_id, tenant_id),
            ).fetchone()
            if row is None:
                return False
            now = _utc_now_iso()
            conn.execute("UPDATE prompt_profiles SET is_active = 0 WHERE tenant_id = ?", (tenant_id,))
            conn.execute(
                "UPDATE prompt_profiles SET is_active = 1, updated_at = ? WHERE id = ?",
                (now, profile_id),
            )
            conn.commit()
            return True

    def get_prompt_overrides(self, profile_id: str) -> dict[str, dict]:
        with self._connect() as conn:
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

    def upsert_prompt_override(
        self,
        profile_id: str,
        component_id: str,
        content: str | None,
        enabled: bool | None,
    ) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM prompt_component_overrides
                WHERE profile_id = ? AND component_id = ?
                """,
                (profile_id, component_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO prompt_component_overrides(
                        id, profile_id, component_id, content, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
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
            conn.commit()

    def reset_prompt_profile(self, profile_id: str) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM prompt_component_overrides WHERE profile_id = ?", (profile_id,))
            conn.execute("UPDATE prompt_profiles SET updated_at = ? WHERE id = ?", (now, profile_id))
            conn.commit()

    def delete_all_data(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM performance_exchanges")
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM conversations")
            conn.execute("DELETE FROM prompt_component_overrides")
            conn.execute("DELETE FROM prompt_profiles")
            conn.execute("DELETE FROM prompt_context_settings")
            conn.commit()

    def export_all_data(self, tenant_id: str) -> dict:
        with self._connect() as conn:
            conversations = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, title, created_at, updated_at FROM conversations ORDER BY created_at ASC"
                ).fetchall()
            ]
            messages = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, conversation_id, role, content, created_at
                    FROM messages
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            ]
            performance = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        id, conversation_id, created_at, user_preview, assistant_preview,
                        total_latency_ms, llm_latency_ms, prompt_tokens, completion_tokens, total_tokens,
                        system_chars, user_chars, assistant_chars,
                        system_tokens_est, user_tokens_est, assistant_tokens_est
                    FROM performance_exchanges
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            ]
            prompt_profiles = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, tenant_id, name, is_default, is_active, created_at, updated_at
                    FROM prompt_profiles
                    WHERE tenant_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant_id,),
                ).fetchall()
            ]
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
            context_settings = conn.execute(
                """
                SELECT tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, compact_instructions, updated_at
                FROM prompt_context_settings
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()

        return {
            "tenant_id": tenant_id,
            "exported_at": _utc_now_iso(),
            "conversations": conversations,
            "messages": messages,
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
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, compact_instructions, updated_at
                FROM prompt_context_settings
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
            if row is None:
                now = _utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO prompt_context_settings(tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, compact_instructions, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, max_context_tokens, max_response_tokens, compact_trigger_pct, default_compaction_instructions(), now),
                )
                conn.commit()
                return StoredContextSettings(
                    tenant_id=tenant_id,
                    max_context_tokens=max_context_tokens,
                    max_response_tokens=max_response_tokens,
                    compact_trigger_pct=compact_trigger_pct,
                    compact_instructions=default_compaction_instructions(),
                    updated_at=_utc_from_iso(now),
                )
            if str(row["compact_instructions"] or "").strip() == "":
                now = _utc_now_iso()
                default_value = default_compaction_instructions()
                conn.execute(
                    """
                    UPDATE prompt_context_settings
                    SET compact_instructions = ?, updated_at = ?
                    WHERE tenant_id = ?
                    """,
                    (default_value, now, tenant_id),
                )
                conn.commit()
                return StoredContextSettings(
                    tenant_id=row["tenant_id"],
                    max_context_tokens=int(row["max_context_tokens"]),
                    max_response_tokens=int(row["max_response_tokens"]),
                    compact_trigger_pct=float(row["compact_trigger_pct"]),
                    compact_instructions=default_value,
                    updated_at=_utc_from_iso(now),
                )
            return StoredContextSettings(
                tenant_id=row["tenant_id"],
                max_context_tokens=int(row["max_context_tokens"]),
                max_response_tokens=int(row["max_response_tokens"]),
                compact_trigger_pct=float(row["compact_trigger_pct"]),
                compact_instructions=str(row["compact_instructions"] or ""),
                updated_at=_utc_from_iso(row["updated_at"]),
            )

    def update_context_settings(
        self,
        tenant_id: str,
        max_context_tokens: int | None = None,
        max_response_tokens: int | None = None,
        compact_trigger_pct: float | None = None,
        compact_instructions: str | None = None,
    ) -> StoredContextSettings:
        current = self.ensure_context_settings(
            tenant_id=tenant_id,
            max_context_tokens=4096,
            max_response_tokens=512,
            compact_trigger_pct=0.9,
        )
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE prompt_context_settings
                SET max_context_tokens = ?,
                    max_response_tokens = ?,
                    compact_trigger_pct = ?,
                    compact_instructions = ?,
                    updated_at = ?
                WHERE tenant_id = ?
                """,
                (
                    int(max_context_tokens if max_context_tokens is not None else current.max_context_tokens),
                    int(max_response_tokens if max_response_tokens is not None else current.max_response_tokens),
                    float(compact_trigger_pct if compact_trigger_pct is not None else current.compact_trigger_pct),
                    str(compact_instructions if compact_instructions is not None else current.compact_instructions),
                    now,
                    tenant_id,
                ),
            )
            conn.commit()

        return self.ensure_context_settings(
            tenant_id=tenant_id,
            max_context_tokens=4096,
            max_response_tokens=512,
            compact_trigger_pct=0.9,
        )
