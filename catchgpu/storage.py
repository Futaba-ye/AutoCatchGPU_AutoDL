"""SQLite 持久化：任务、创建意图、事件与通知设置。"""

import sqlite3
from pathlib import Path

from catchgpu.models import NotificationSettings, Task, now, uid


class TaskNotFound(LookupError):
    """用户请求的任务不存在，与平台响应字段缺失区分。"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, rule_id TEXT NOT NULL,
                name TEXT NOT NULL UNIQUE, state TEXT NOT NULL, instance_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, level TEXT NOT NULL,
                message TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id, id);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS favorites (
                provider TEXT NOT NULL, gpu_id TEXT NOT NULL, name TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(provider, gpu_id)
            );
        """
        )
        self.db.commit()

    def save(self, task: Task):
        task.updated_at = now()
        self.db.execute(
            "INSERT OR REPLACE INTO tasks VALUES (?, ?)", (task.id, task.model_dump_json())
        )
        self.db.commit()

    def get(self, task_id: str) -> Task:
        row = self.db.execute("SELECT body FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise TaskNotFound(task_id)
        return Task.model_validate_json(row["body"])

    def tasks(self) -> list[Task]:
        return [
            Task.model_validate_json(row["body"])
            for row in self.db.execute("SELECT body FROM tasks ORDER BY rowid DESC")
        ]

    def event(self, task_id: str | None, level: str, message: str, kind: str = "log"):
        self.db.execute(
            "INSERT INTO events(task_id,level,message,kind,created_at) VALUES(?,?,?,?,?)",
            (task_id, level, message, kind, now()),
        )
        self.db.commit()

    def events(
        self, after: int = 0, task_id: str | None = None, level: str | None = None, limit: int = 200
    ):
        clauses, values = ["id > ?"], [after]
        if task_id:
            clauses.append("task_id = ?")
            values.append(task_id)
        if level:
            clauses.append("level = ?")
            values.append(level)
        values.append(limit)
        # 只拼接固定 SQL 片段，筛选值均通过占位符绑定。
        direction = "ASC" if after else "DESC"
        query = (
            "SELECT * FROM events WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY id {direction} LIMIT ?"
        )
        rows = self.db.execute(query, values).fetchall()
        return [dict(row) for row in (rows if after else reversed(rows))]

    def begin_attempt(self, task: Task, rule_id: str, name: str) -> str:
        attempt_id = uid()
        self.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?)",
            (attempt_id, task.id, rule_id, name, "pending", None, now()),
        )
        self.db.commit()
        return attempt_id

    def pending(self, task_id: str):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM attempts WHERE task_id=? AND state='pending'", (task_id,)
            )
        ]

    def finish_attempt(self, attempt_id: str, state: str, instance_id: str | None = None):
        self.db.execute(
            "UPDATE attempts SET state=?,instance_id=? WHERE id=?", (state, instance_id, attempt_id)
        )
        self.db.commit()

    def commit_instance(self, task: Task, instance, attempt_id: str):
        """实例结果与创建意图一起提交，避免进程中断后重复创建。"""
        if instance.id not in {item.id for item in task.instances}:
            task.instances.append(instance)
        task.updated_at = now()
        with self.db:
            self.db.execute(
                "UPDATE attempts SET state='success',instance_id=? WHERE id=?",
                (instance.id, attempt_id),
            )
            self.db.execute("UPDATE tasks SET body=? WHERE id=?", (task.model_dump_json(), task.id))

    def notification_settings(self) -> NotificationSettings:
        row = self.db.execute("SELECT body FROM settings WHERE key='notifications'").fetchone()
        return (
            NotificationSettings.model_validate_json(row["body"]) if row else NotificationSettings()
        )

    def set_notifications(self, settings: NotificationSettings):
        self.db.execute(
            "INSERT OR REPLACE INTO settings VALUES('notifications',?)",
            (settings.model_dump_json(),),
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def favorites(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM favorites ORDER BY created_at,provider,gpu_id"
            )
        ]

    def save_favorite(self, provider: str, gpu_id: str, name: str):
        self.db.execute(
            "INSERT INTO favorites VALUES(?,?,?,?) ON CONFLICT(provider,gpu_id) DO UPDATE SET name=excluded.name",
            (provider, gpu_id, name, now()),
        )
        self.db.commit()

    def remove_favorite(self, provider: str, gpu_id: str):
        self.db.execute("DELETE FROM favorites WHERE provider=? AND gpu_id=?", (provider, gpu_id))
        self.db.commit()
