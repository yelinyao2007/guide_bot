import json
from sqlalchemy import create_engine, text
from .domain import Task, TRANSITIONS, now
from .robot.base import RobotError


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        with self.engine.begin() as db:
            db.execute(text("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL)"))
            db.execute(text("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)"))

    def _emit(self, db, kind, payload):
        envelope = {"version": 1, "type": kind, "timestamp": now(), "payload": payload}
        row = db.execute(text("INSERT INTO events(body) VALUES (:body)"), {"body": json.dumps(envelope, ensure_ascii=False)})
        return {"seq": row.lastrowid, **envelope}

    def emit(self, kind, payload):
        with self.engine.begin() as db:
            return self._emit(db, kind, payload)

    def add(self, task):
        with self.engine.begin() as db:
            db.execute(text("INSERT INTO tasks VALUES (:id,:body)"), {"id": task.id, "body": task.model_dump_json()})
            self._emit(db, "task.updated", task.model_dump())
        return task

    def get(self, task_id):
        with self.engine.connect() as db:
            body = db.execute(text("SELECT body FROM tasks WHERE id=:id"), {"id": task_id}).scalar()
        if body is None:
            raise RobotError("NOT_FOUND")
        return Task.model_validate_json(body)

    def tasks(self):
        with self.engine.connect() as db:
            return [Task.model_validate_json(row[0]) for row in db.execute(text("SELECT body FROM tasks ORDER BY rowid"))]

    def transition(self, task_id, target, error=None, result=None):
        with self.engine.begin() as db:
            body = db.execute(text("SELECT body FROM tasks WHERE id=:id"), {"id": task_id}).scalar()
            if body is None:
                raise RobotError("NOT_FOUND")
            task = Task.model_validate_json(body)
            if target not in TRANSITIONS.get(task.status, set()):
                raise RobotError("INVALID_TRANSITION")
            task.status, task.error_code, task.result, task.updated_at = target, error, result, now()
            changed = db.execute(text("UPDATE tasks SET body=:new WHERE id=:id AND body=:old"),
                {"new": task.model_dump_json(), "id": task_id, "old": body})
            if changed.rowcount != 1:
                raise RobotError("INVALID_TRANSITION")
            self._emit(db, "task.updated", task.model_dump())
            return task

    def events(self, after=0, limit=100):
        with self.engine.connect() as db:
            return [{"seq": row[0], **json.loads(row[1])} for row in db.execute(
                text("SELECT seq,body FROM events WHERE seq>:after ORDER BY seq LIMIT :limit"), {"after": after, "limit": limit})]
