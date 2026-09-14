"""SQLite local operational registry for Tool 1 processing state and audit trail."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..models import ParserResult, RenameProposal


class LocalRegistry:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                tracking_id TEXT PRIMARY KEY,
                original_path TEXT NOT NULL,
                current_path TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                current_filename TEXT NOT NULL,
                proposed_filename TEXT,
                when_val TEXT,
                who_val TEXT,
                what_val TEXT,
                where_val TEXT,
                status TEXT NOT NULL, -- pending, approved, committed, deferred, error
                needs_review INTEGER NOT NULL,
                review_reasons TEXT,
                parser_result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS rename_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_id TEXT NOT NULL,
                from_path TEXT NOT NULL,
                to_path TEXT NOT NULL,
                from_filename TEXT NOT NULL,
                to_filename TEXT NOT NULL,
                mode TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS review_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                changes_json TEXT NOT NULL,
                previous_values_json TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_db_reviews (
                tracking_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                selected_media_row_id INTEGER,
                database_state TEXT NOT NULL,
                snapshot_timestamp TEXT NOT NULL,
                result_json TEXT NOT NULL,
                review_required INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_original_path ON files(original_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_current_path ON files(current_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_db_decision ON media_db_reviews(decision)")
            conn.commit()

    def get_file(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["parser_result"] = json.loads(d["parser_result_json"])
                d["review_reasons"] = json.loads(d["review_reasons"]) if d["review_reasons"] else []
                return d
        return None

    def find_tracking_id_by_path(self, file_path: Path) -> Optional[str]:
        """Return the newest tracking ID already associated with this physical path.

        Dry-runs do not write the temporary ID into the source filename, so path lookup is
        required to make repeated analysis idempotent instead of generating a new registry
        row on every scan.
        """
        normalized = str(Path(file_path).expanduser().resolve())
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tracking_id
                FROM files
                WHERE original_path = ? OR current_path = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized, normalized),
            )
            row = cursor.fetchone()
            return str(row["tracking_id"]) if row else None

    def list_files(self, status: Optional[str] = None, needs_review: Optional[bool] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM files WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if needs_review is not None:
            query += " AND needs_review = ?"
            params.append(1 if needs_review else 0)
        query += " ORDER BY updated_at DESC"

        results = []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                d = dict(row)
                d["parser_result"] = json.loads(d["parser_result_json"])
                d["review_reasons"] = json.loads(d["review_reasons"]) if d["review_reasons"] else []
                results.append(d)
        return results

    def save_proposal(self, proposal: RenameProposal):
        now = datetime.now(timezone.utc).isoformat()
        pr = proposal.parser_result
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO files (
                tracking_id, original_path, current_path, original_filename, current_filename,
                proposed_filename, when_val, who_val, what_val, where_val, status,
                needs_review, review_reasons, parser_result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                current_path = excluded.current_path,
                current_filename = excluded.current_filename,
                proposed_filename = excluded.proposed_filename,
                when_val = excluded.when_val,
                what_val = excluded.what_val,
                where_val = excluded.where_val,
                status = excluded.status,
                needs_review = excluded.needs_review,
                review_reasons = excluded.review_reasons,
                parser_result_json = excluded.parser_result_json,
                updated_at = excluded.updated_at
            """, (
                proposal.tracking_id,
                proposal.original_path,
                str(Path(proposal.original_path)),
                pr.identity.original_filename,
                proposal.current_filename,
                proposal.proposed_filename,
                pr.when.selected_value,
                pr.who,
                pr.what.selected_value,
                f"{pr.where.place_location or ''}-{pr.where.country_iso2 or ''}".strip("-"),
                proposal.status,
                1 if proposal.needs_review else 0,
                json.dumps(proposal.review_reasons),
                pr.model_dump_json(),
                now,
                now
            ))
            conn.commit()

    def record_commit(self, proposal: RenameProposal, new_path: Path):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # Update files table
            cursor.execute("""
            UPDATE files SET
                current_path = ?,
                current_filename = ?,
                status = 'committed',
                updated_at = ?
            WHERE tracking_id = ?
            """, (str(new_path), new_path.name, now, proposal.tracking_id))

            # Record history
            cursor.execute("""
            INSERT INTO rename_history (
                tracking_id, from_path, to_path, from_filename, to_filename, mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                proposal.tracking_id,
                proposal.original_path,
                str(new_path),
                proposal.current_filename,
                new_path.name,
                proposal.mode.value,
                now
            ))
            conn.commit()

    def update_status(self, tracking_id: str, new_status: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE files SET status = ?, updated_at = ? WHERE tracking_id = ?
            """, (new_status, now, tracking_id))
            conn.commit()

    def record_review_action(
        self,
        tracking_id: str,
        action: str,
        reviewer: str,
        changes: Dict[str, Any],
        previous_values: Dict[str, Any],
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO review_actions (
                tracking_id, action, reviewer, changes_json, previous_values_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                tracking_id,
                action,
                reviewer,
                json.dumps(changes),
                json.dumps(previous_values),
                now
            ))
            conn.commit()

    def update_file_review(
        self,
        tracking_id: str,
        when_val: str,
        what_val: str,
        where_val: str,
        proposed_filename: str,
        status: str,
        needs_review: bool,
        review_reasons: List[str],
        parser_result_json: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE files SET
                when_val = ?,
                what_val = ?,
                where_val = ?,
                proposed_filename = ?,
                status = ?,
                needs_review = ?,
                review_reasons = ?,
                parser_result_json = ?,
                updated_at = ?
            WHERE tracking_id = ?
            """, (
                when_val,
                what_val,
                where_val,
                proposed_filename,
                status,
                1 if needs_review else 0,
                json.dumps(review_reasons),
                parser_result_json,
                now,
                tracking_id
            ))
            conn.commit()

    def get_review_actions(self, tracking_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM review_actions WHERE tracking_id = ? ORDER BY id ASC",
                (tracking_id,)
            )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["changes"] = json.loads(d["changes_json"])
                d["previous_values"] = json.loads(d["previous_values_json"])
                results.append(d)
            return results

    def get_history(self, tracking_id: str) -> List[Dict[str, Any]]:
        return self.get_review_actions(tracking_id)

    def save_media_db_review(
        self,
        tracking_id: str,
        decision: str,
        database_state: str,
        snapshot_timestamp: str,
        result_json: str,
        selected_media_row_id: Optional[int] = None,
        review_required: bool = False,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO media_db_reviews (
                tracking_id, decision, selected_media_row_id, database_state,
                snapshot_timestamp, result_json, review_required, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                decision = excluded.decision,
                selected_media_row_id = excluded.selected_media_row_id,
                database_state = excluded.database_state,
                snapshot_timestamp = excluded.snapshot_timestamp,
                result_json = excluded.result_json,
                review_required = excluded.review_required,
                updated_at = excluded.updated_at
            """, (
                tracking_id,
                decision,
                selected_media_row_id,
                database_state,
                snapshot_timestamp,
                result_json,
                1 if review_required else 0,
                now,
            ))
            conn.commit()

    def get_media_db_review(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_db_reviews WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["result"] = json.loads(d["result_json"])
                return d
        return None

    def list_media_db_reviews(
        self,
        decision: Optional[str] = None,
        review_required: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM media_db_reviews WHERE 1=1"
        params = []
        if decision:
            query += " AND decision = ?"
            params.append(decision)
        if review_required is not None:
            query += " AND review_required = ?"
            params.append(1 if review_required else 0)
        query += " ORDER BY updated_at DESC"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["result"] = json.loads(d["result_json"])
                results.append(d)
            return results

