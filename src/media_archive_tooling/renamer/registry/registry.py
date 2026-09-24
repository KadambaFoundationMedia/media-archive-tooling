"""SQLite local operational registry for Tool 1 processing state and audit trail."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union

from ..models import ParserResult, RenameProposal


class LocalRegistry:
    _memory_lock = threading.Lock()

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
            CREATE TABLE IF NOT EXISTS registry_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
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
                proposal_mode TEXT,
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
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS travel_reviews (
                tracking_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                reference_checksum TEXT NOT NULL,
                reference_row_count INTEGER NOT NULL,
                selected_row_ids TEXT,
                result_json TEXT NOT NULL,
                applied_enrichment INTEGER NOT NULL DEFAULT 0,
                tool2_decision TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_db_syncs (
                tracking_id TEXT PRIMARY KEY,
                sync_status TEXT NOT NULL,
                operation_type TEXT,
                media_row_id INTEGER,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                error_message TEXT,
                request_json TEXT,
                result_json TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_row_ledger (
                row_id INTEGER PRIMARY KEY,
                table_id TEXT NOT NULL,
                tracking_id TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                session_id TEXT NOT NULL,
                marker TEXT NOT NULL,
                status TEXT NOT NULL, -- CREATED, PURGING, PURGED, PURGE_BLOCKED
                error_message TEXT,
                details_json TEXT,
                updated_at TEXT NOT NULL
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS content_reviews (
                tracking_id TEXT PRIMARY KEY,
                classification TEXT NOT NULL,
                confidence TEXT NOT NULL,
                mantra_type TEXT NOT NULL,
                process_by_tool_6 INTEGER NOT NULL,
                cutter_proposal_json TEXT,
                transcript_path TEXT NOT NULL,
                transcript_sha256 TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                derived_audio_path TEXT,
                result_json TEXT NOT NULL,
                review_required INTEGER NOT NULL,
                review_reason TEXT,
                human_decision_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_audio_derivatives (
                derived_path TEXT PRIMARY KEY,
                source_video_path TEXT NOT NULL,
                source_video_tracking_id TEXT NOT NULL,
                source_video_sha256 TEXT NOT NULL,
                derived_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stage_checkpoints (
                tracking_id TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                input_path TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                status TEXT NOT NULL, -- COMPLETED, FAILED, REVIEW_REQUIRED, SKIPPED
                summary TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tracking_id, stage_name)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS scratch_artifacts (
                artifact_path TEXT PRIMARY KEY,
                run_id TEXT,
                tracking_id TEXT,
                created_at TEXT NOT NULL
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_splits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_tracking_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_duration_seconds REAL NOT NULL,
                cut_point_seconds REAL NOT NULL,
                singing_tracking_id TEXT NOT NULL,
                singing_path TEXT NOT NULL,
                singing_sha256 TEXT NOT NULL,
                singing_duration_seconds REAL NOT NULL,
                singing_leading_silence_seconds REAL NOT NULL DEFAULT 0.0,
                singing_pending_tool_11_move INTEGER NOT NULL DEFAULT 1,
                class_tracking_id TEXT NOT NULL,
                class_path TEXT NOT NULL,
                class_sha256 TEXT NOT NULL,
                class_duration_seconds REAL NOT NULL,
                class_leading_silence_seconds REAL NOT NULL DEFAULT 0.0,
                class_pending_tool_11_move INTEGER NOT NULL DEFAULT 1,
                tool_version TEXT NOT NULL,
                evidence_ids_json TEXT,
                split_details_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_tracking_id) REFERENCES files (tracking_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS human_cut_decisions (
                tracking_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                cut_point_seconds REAL NOT NULL,
                reviewer TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tracking_id) REFERENCES files (tracking_id)
            )
            """)
            try:
                cursor.execute("ALTER TABLE travel_reviews ADD COLUMN tool2_decision TEXT")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE files ADD COLUMN proposal_mode TEXT")
            except Exception:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_original_path ON files(original_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_current_path ON files(current_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_db_decision ON media_db_reviews(decision)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_travel_reviews_decision ON travel_reviews(decision)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_db_syncs_status ON media_db_syncs(sync_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_row_ledger_status ON test_row_ledger(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_row_ledger_tracking_id ON test_row_ledger(tracking_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_row_ledger_session_id ON test_row_ledger(session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_reviews_classification ON content_reviews(classification)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_reviews_review_required ON content_reviews(review_required)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_video_audio_derivatives_source ON video_audio_derivatives(source_video_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stage_checkpoints_tid ON stage_checkpoints(tracking_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scratch_artifacts_run_id ON scratch_artifacts(run_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_splits_source ON file_splits(source_tracking_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_splits_singing ON file_splits(singing_tracking_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_splits_class ON file_splits(class_tracking_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_human_cut_decisions_tid ON human_cut_decisions(tracking_id)")
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
        normalized = str(file_path.resolve())
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tracking_id FROM files
                WHERE current_path = ? OR original_path = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized, normalized),
            )
            row = cursor.fetchone()
            return str(row["tracking_id"]) if row else None

    def get_latest_rename(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest committed rename for safe repeat-rename reconciliation."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM rename_history
                WHERE tracking_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (tracking_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_rename_history(self, tracking_id: str) -> List[Dict[str, Any]]:
        """Return all committed renames for a tracking ID in chronological order."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM rename_history WHERE tracking_id = ? ORDER BY id ASC",
                (tracking_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

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

        def _read_rows() -> List[Dict[str, Any]]:
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

        try:
            return _read_rows()
        except sqlite3.OperationalError as exc:
            # A registry can be deliberately deleted while a portal process is
            # still alive. SQLite then recreates an empty file on the next
            # connection, without its tables. Initialize that empty registry
            # and present an empty portal rather than returning HTTP 500.
            if "no such table: files" not in str(exc).lower():
                raise
            self._init_db()
            return _read_rows()

    def save_proposal(self, proposal: RenameProposal):
        now = datetime.now(timezone.utc).isoformat()
        pr = proposal.parser_result
        mode_val = proposal.mode.value if hasattr(proposal.mode, "value") else str(proposal.mode)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO files (
                tracking_id, original_path, current_path, original_filename, current_filename,
                proposed_filename, when_val, who_val, what_val, where_val, status,
                needs_review, review_reasons, parser_result_json, proposal_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                proposal_mode = excluded.proposal_mode,
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
                mode_val,
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

    def update_file_status(
        self,
        tracking_id: str,
        status: Optional[str] = None,
        current_path: Optional[Union[str, Path]] = None,
        proposed_filename: Optional[str] = None,
        what_val: Optional[str] = None,
    ):
        """Update file status, current_path, proposed_filename, and/or what_val in files table."""
        now = datetime.now(timezone.utc).isoformat()
        updates = ["updated_at = ?"]
        params: List[Any] = [now]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if current_path is not None:
            cp = Path(current_path)
            updates.append("current_path = ?")
            params.append(str(cp))
            updates.append("current_filename = ?")
            params.append(cp.name)
        if proposed_filename is not None:
            updates.append("proposed_filename = ?")
            params.append(proposed_filename)
        if what_val is not None:
            updates.append("what_val = ?")
            params.append(what_val)
        params.append(tracking_id)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE files SET {', '.join(updates)} WHERE tracking_id = ?", params)
            conn.commit()

    update_file_path = update_file_status

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

    def save_travel_review(
        self,
        tracking_id: str,
        decision: str,
        reference_checksum: str,
        reference_row_count: int,
        result_json: str,
        selected_row_ids: Optional[List[int]] = None,
        applied_enrichment: bool = False,
        tool2_decision: Optional[str] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        sel_ids_str = json.dumps(selected_row_ids) if selected_row_ids else "[]"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO travel_reviews (
                tracking_id, decision, reference_checksum, reference_row_count,
                selected_row_ids, result_json, applied_enrichment, tool2_decision, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                decision = excluded.decision,
                reference_checksum = excluded.reference_checksum,
                reference_row_count = excluded.reference_row_count,
                selected_row_ids = excluded.selected_row_ids,
                result_json = excluded.result_json,
                applied_enrichment = excluded.applied_enrichment,
                tool2_decision = excluded.tool2_decision,
                updated_at = excluded.updated_at
            """, (
                tracking_id,
                decision,
                reference_checksum,
                reference_row_count,
                sel_ids_str,
                result_json,
                1 if applied_enrichment else 0,
                tool2_decision,
                now,
            ))
            conn.commit()

    def get_travel_review(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM travel_reviews WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["result"] = json.loads(d["result_json"])
                d["selected_row_ids"] = json.loads(d["selected_row_ids"]) if d.get("selected_row_ids") else []
                return d
        return None

    def list_travel_reviews(
        self,
        decision: Optional[str] = None,
        applied_enrichment: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM travel_reviews WHERE 1=1"
        params = []
        if decision:
            query += " AND decision = ?"
            params.append(decision)
        if applied_enrichment is not None:
            query += " AND applied_enrichment = ?"
            params.append(1 if applied_enrichment else 0)
        query += " ORDER BY updated_at DESC"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["result"] = json.loads(d["result_json"])
                d["selected_row_ids"] = json.loads(d["selected_row_ids"]) if d.get("selected_row_ids") else []
                results.append(d)
            return results

    def save_media_db_sync(
        self,
        tracking_id: str,
        sync_status: str,
        operation_type: Optional[str] = None,
        media_row_id: Optional[int] = None,
        attempt_count: int = 1,
        last_attempt_at: Optional[str] = None,
        error_message: Optional[str] = None,
        request_json: Optional[str] = None,
        result_json: Optional[str] = None,
    ):
        from ...media_db_updater.write_adapter import redact_secrets

        if request_json is not None:
            request_json = redact_secrets(request_json)
        if result_json is not None:
            result_json = redact_secrets(result_json)
        if error_message is not None:
            error_message = redact_secrets(error_message)

        now = datetime.now(timezone.utc).isoformat()
        last_attempt = last_attempt_at or now
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO media_db_syncs (
                tracking_id, sync_status, operation_type, media_row_id,
                attempt_count, last_attempt_at, error_message, request_json, result_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                sync_status = excluded.sync_status,
                operation_type = excluded.operation_type,
                media_row_id = excluded.media_row_id,
                attempt_count = excluded.attempt_count,
                last_attempt_at = excluded.last_attempt_at,
                error_message = excluded.error_message,
                request_json = COALESCE(excluded.request_json, media_db_syncs.request_json),
                result_json = excluded.result_json,
                updated_at = excluded.updated_at
            """, (
                tracking_id,
                sync_status,
                operation_type,
                media_row_id,
                attempt_count,
                last_attempt,
                error_message,
                request_json,
                result_json,
                now,
            ))
            conn.commit()

    def get_media_db_sync(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_db_syncs WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["request"] = json.loads(d["request_json"]) if d.get("request_json") else None
                d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
                return d
        return None

    def list_media_db_syncs(
        self,
        sync_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM media_db_syncs WHERE 1=1"
        params = []
        if sync_status:
            query += " AND sync_status = ?"
            params.append(sync_status)
        query += " ORDER BY updated_at DESC"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["request"] = json.loads(d["request_json"]) if d.get("request_json") else None
                d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
                results.append(d)
            return results

    def list_pending_media_db_syncs(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM media_db_syncs WHERE sync_status IN ('PENDING_SYNC', 'FAILED_RETRYABLE', 'DATABASE_UNAVAILABLE') ORDER BY updated_at ASC"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["request"] = json.loads(d["request_json"]) if d.get("request_json") else None
                d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
                results.append(d)
            return results

    def record_test_created_row(
        self,
        table_id: str,
        row_id: int,
        tracking_id: str,
        request_fingerprint: str,
        session_id: str,
        marker: str,
        run_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        status: str = "CREATED",
        error_message: Optional[str] = None,
    ):
        """Record or update a test-created row in the durable test_row_ledger."""
        from ...media_db_updater.write_adapter import redact_secrets

        now = datetime.now(timezone.utc).isoformat()
        redacted_err = redact_secrets(error_message) if error_message else None
        details_json = json.dumps(redact_secrets(details)) if details else None

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO test_row_ledger (
                row_id, table_id, tracking_id, run_id, created_at,
                request_fingerprint, session_id, marker, status,
                error_message, details_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO UPDATE SET
                table_id = excluded.table_id,
                tracking_id = excluded.tracking_id,
                run_id = COALESCE(excluded.run_id, test_row_ledger.run_id),
                request_fingerprint = excluded.request_fingerprint,
                session_id = excluded.session_id,
                marker = excluded.marker,
                status = excluded.status,
                error_message = excluded.error_message,
                details_json = COALESCE(excluded.details_json, test_row_ledger.details_json),
                updated_at = excluded.updated_at
            """, (
                row_id,
                str(table_id),
                tracking_id,
                run_id,
                now,
                request_fingerprint,
                session_id,
                marker,
                status,
                redacted_err,
                details_json,
                now,
            ))
            conn.commit()

    def get_test_row(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a single test row ledger record by row_id."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM test_row_ledger WHERE row_id = ?", (row_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["details"] = json.loads(d["details_json"]) if d.get("details_json") else None
                return d
        return None

    def list_test_rows(
        self,
        status: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List test row ledger records with optional status or session_id filter."""
        query = "SELECT * FROM test_row_ledger WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY row_id ASC"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d["details_json"]) if d.get("details_json") else None
                results.append(d)
            return results

    def update_test_row_status(
        self,
        row_id: int,
        status: str,
        error_message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Update lifecycle status and error/details for a test row ledger record."""
        from ...media_db_updater.write_adapter import redact_secrets

        now = datetime.now(timezone.utc).isoformat()
        redacted_err = redact_secrets(error_message) if error_message else None
        details_json = json.dumps(redact_secrets(details)) if details else None

        with self._get_conn() as conn:
            cursor = conn.cursor()
            if details_json is not None:
                cursor.execute("""
                UPDATE test_row_ledger SET
                    status = ?,
                    error_message = ?,
                    details_json = ?,
                    updated_at = ?
                WHERE row_id = ?
                """, (status, redacted_err, details_json, now, row_id))
            else:
                cursor.execute("""
                UPDATE test_row_ledger SET
                    status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE row_id = ?
                """, (status, redacted_err, now, row_id))
            conn.commit()

    def get_metadata(self, key: str) -> Optional[str]:
        """Retrieve a metadata string value by key."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM registry_metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None

    def set_metadata(self, key: str, value: str):
        """Set or replace a metadata string value by key."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO registry_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            """, (key, value, now))
            conn.commit()

    def delete_metadata(self, key: str):
        """Remove a metadata key if present."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM registry_metadata WHERE key = ?", (key,))
            conn.commit()

    def clear_review_state(self):
        """Atomically clear all local review/run/proposal/sync and ledger state for a fresh test slate."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM review_actions")
            cursor.execute("DELETE FROM rename_history")
            cursor.execute("DELETE FROM media_db_reviews")
            cursor.execute("DELETE FROM travel_reviews")
            cursor.execute("DELETE FROM media_db_syncs")
            cursor.execute("DELETE FROM content_reviews")
            cursor.execute("DELETE FROM video_audio_derivatives")
            cursor.execute("DELETE FROM stage_checkpoints")
            cursor.execute("DELETE FROM scratch_artifacts")
            cursor.execute("DELETE FROM file_splits")
            cursor.execute("DELETE FROM human_cut_decisions")
            cursor.execute("DELETE FROM files")
            cursor.execute("DELETE FROM test_row_ledger")
            conn.commit()

    def save_content_review(self, result: Any):
        """Persist or replace Tool 5 content discovery result."""
        now = datetime.now(timezone.utc).isoformat()
        res_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        cutter_json = json.dumps(res_dict.get("cutter_proposal")) if res_dict.get("cutter_proposal") else None
        human_json = json.dumps(res_dict.get("human_decision")) if res_dict.get("human_decision") else None
        classification_val = res_dict["classification"].value if hasattr(res_dict["classification"], "value") else str(res_dict["classification"])
        confidence_val = res_dict["confidence"].value if hasattr(res_dict["confidence"], "value") else str(res_dict["confidence"])
        mantra_val = res_dict["mantra_type"].value if hasattr(res_dict["mantra_type"], "value") else str(res_dict["mantra_type"])

        input_sha = res_dict.get("input_sha256") or res_dict.get("source_sha256", "")
        if not res_dict.get("input_sha256"):
            res_dict["input_sha256"] = input_sha
        if not res_dict.get("source_sha256"):
            res_dict["source_sha256"] = input_sha

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO content_reviews (
                tracking_id, classification, confidence, mantra_type, process_by_tool_6,
                cutter_proposal_json, transcript_path, transcript_sha256, input_sha256,
                source_path, derived_audio_path, result_json, review_required, review_reason,
                human_decision_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                res_dict["tracking_id"],
                classification_val,
                confidence_val,
                mantra_val,
                1 if res_dict.get("process_by_tool_6") else 0,
                cutter_json,
                res_dict.get("transcript_path", ""),
                res_dict.get("transcript_sha256", ""),
                input_sha,
                res_dict.get("source_path", ""),
                res_dict.get("derived_audio_path"),
                json.dumps(res_dict),
                1 if res_dict.get("review_required") else 0,
                res_dict.get("review_reason"),
                human_json,
                res_dict.get("created_at", now),
                res_dict.get("updated_at", now),
            ))
            conn.commit()

    def get_content_review(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve Tool 5 content discovery review record by tracking_id."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM content_reviews WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
                d["cutter_proposal"] = json.loads(d["cutter_proposal_json"]) if d.get("cutter_proposal_json") else None
                d["human_decision"] = json.loads(d["human_decision_json"]) if d.get("human_decision_json") else None
                if d.get("input_sha256") and not d.get("source_sha256"):
                    d["source_sha256"] = d["input_sha256"]
                return d
        return None

    def list_content_reviews(self, review_required: Optional[bool] = None) -> List[Dict[str, Any]]:
        """List content reviews with optional review_required filter."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if review_required is not None:
                cursor.execute(
                    "SELECT * FROM content_reviews WHERE review_required = ? ORDER BY updated_at DESC",
                    (1 if review_required else 0,),
                )
            else:
                cursor.execute("SELECT * FROM content_reviews ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
                d["cutter_proposal"] = json.loads(d["cutter_proposal_json"]) if d.get("cutter_proposal_json") else None
                d["human_decision"] = json.loads(d["human_decision_json"]) if d.get("human_decision_json") else None
                out.append(d)
            return out

    def save_content_review_human_decision(
        self,
        tracking_id: str,
        classification: str,
        mantra_type: str,
        process_by_tool_6: int,
        review_required: int,
        human_decision_json: Dict[str, Any],
        updated_at: str,
        cutter_proposal_json: Optional[str] = None,
        review_reason: Optional[str] = None,
    ):
        """Update content review record with human operator decision and synchronized result_json."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT result_json, cutter_proposal_json, review_reason FROM content_reviews WHERE tracking_id = ?",
                (tracking_id,),
            )
            row = cursor.fetchone()

            existing_result = {}
            if row and row["result_json"]:
                try:
                    existing_result = json.loads(row["result_json"])
                except Exception:
                    existing_result = {}

            existing_result["classification"] = classification
            existing_result["mantra_type"] = mantra_type
            existing_result["process_by_tool_6"] = bool(process_by_tool_6)
            existing_result["review_required"] = bool(review_required)
            existing_result["human_decision"] = human_decision_json
            existing_result["updated_at"] = updated_at

            final_cutter_json = row["cutter_proposal_json"] if row else None
            if cutter_proposal_json is not None:
                final_cutter_json = cutter_proposal_json

            if final_cutter_json:
                try:
                    existing_result["cutter_proposal"] = json.loads(final_cutter_json)
                except Exception:
                    existing_result["cutter_proposal"] = None
            else:
                existing_result["cutter_proposal"] = None

            final_review_reason = row["review_reason"] if row else None
            if review_reason is not None:
                final_review_reason = review_reason
            existing_result["review_reason"] = final_review_reason

            cursor.execute("""
            UPDATE content_reviews
            SET classification = ?,
                mantra_type = ?,
                process_by_tool_6 = ?,
                review_required = ?,
                cutter_proposal_json = ?,
                review_reason = ?,
                result_json = ?,
                human_decision_json = ?,
                updated_at = ?
            WHERE tracking_id = ?
            """, (
                classification,
                mantra_type,
                process_by_tool_6,
                review_required,
                final_cutter_json,
                final_review_reason,
                json.dumps(existing_result),
                json.dumps(human_decision_json),
                updated_at,
                tracking_id,
            ))
            conn.commit()

    def register_file(
        self,
        tracking_id: str,
        current_path: Union[str, Path],
        original_path: Optional[Union[str, Path]] = None,
        original_filename: Optional[str] = None,
        current_filename: Optional[str] = None,
        proposed_filename: Optional[str] = None,
        when_val: Optional[str] = None,
        who_val: Optional[str] = None,
        what_val: Optional[str] = None,
        where_val: Optional[str] = None,
        status: str = "PENDING",
        needs_review: bool = False,
        review_reasons: Optional[List[str]] = None,
        parser_result_json: Optional[str] = None,
        proposal_mode: str = "DRY_RUN",
        source_hash: Optional[str] = None,
    ):
        """Convenience registration of file record for tests or external tracking."""
        now = datetime.now(timezone.utc).isoformat()
        c_path = Path(current_path)
        o_path = Path(original_path) if original_path else c_path
        cur_fn = current_filename or c_path.name
        orig_fn = original_filename or o_path.name
        prop_fn = proposed_filename or cur_fn
        pr_json = parser_result_json
        if not pr_json or pr_json == "{}":
            pr_dict = {
                "identity": {
                    "tracking_id": tracking_id,
                    "original_filename": orig_fn,
                    "original_path": str(o_path.resolve()),
                    "current_filename": cur_fn,
                    "extension": c_path.suffix.lower(),
                },
                "context": {"parent_folder": c_path.parent.name},
                "when": {"selected_value": when_val or "2023-01-01", "precision": "day", "state": "EXACT"},
                "what": {"selected_value": what_val or "Class", "state": "EXACT"},
                "where": {"place_location": where_val or "Unknown", "country": "", "country_iso2": "", "state": "EXACT"},
                "who": who_val or "KKS",
                "review_reasons": review_reasons or [],
            }
            pr_json = json.dumps(pr_dict)
        rev_reasons = review_reasons or []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO files (
                tracking_id, original_path, current_path, original_filename, current_filename,
                proposed_filename, when_val, who_val, what_val, where_val, status,
                needs_review, review_reasons, parser_result_json, proposal_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                current_path = excluded.current_path,
                current_filename = excluded.current_filename,
                proposed_filename = excluded.proposed_filename,
                status = excluded.status,
                needs_review = excluded.needs_review,
                review_reasons = excluded.review_reasons,
                updated_at = excluded.updated_at
            """, (
                tracking_id,
                str(o_path.resolve()),
                str(c_path.resolve()),
                orig_fn,
                cur_fn,
                prop_fn,
                when_val or "",
                who_val or "",
                what_val or "",
                where_val or "",
                status,
                1 if needs_review else 0,
                json.dumps(rev_reasons),
                pr_json,
                proposal_mode,
                now,
                now,
            ))
            conn.commit()

    def record_video_audio_derivative(
        self,
        derived_path: Union[str, Path] = "",
        source_video_path: Union[str, Path] = "",
        source_video_tracking_id: str = "",
        source_video_sha256: str = "",
        derived_sha256: str = "",
        **kwargs,
    ):
        """Record video-derived MP3 to prevent re-discovery as an independent file."""
        now = datetime.now(timezone.utc).isoformat()
        final_derived_path = kwargs.get("derived_audio_path") or derived_path
        final_source_path = kwargs.get("source_video_path") or source_video_path
        final_tracking_id = kwargs.get("tracking_id") or source_video_tracking_id
        final_source_sha = kwargs.get("source_sha256") or source_video_sha256
        final_deriv_sha = kwargs.get("derived_sha256") or derived_sha256

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO video_audio_derivatives (
                derived_path, source_video_path, source_video_tracking_id,
                source_video_sha256, derived_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(Path(final_derived_path).resolve()),
                str(Path(final_source_path).resolve()),
                str(final_tracking_id),
                str(final_source_sha),
                str(final_deriv_sha),
                now,
            ))
            conn.commit()

    def get_video_audio_derivative(self, path_or_id: Union[str, Path]) -> Optional[Dict[str, Any]]:
        """Retrieve video-audio derivative metadata if registered by path or tracking_id."""
        val = str(path_or_id)
        resolved = str(Path(val).resolve()) if ("/" in val or "\\" in val) else val
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM video_audio_derivatives
            WHERE derived_path = ? OR source_video_tracking_id = ? OR source_video_path = ?
            ORDER BY created_at DESC LIMIT 1
            """, (resolved, val, resolved))
            row = cursor.fetchone()
            return dict(row) if row else None

    def is_video_audio_derivative(self, path: Union[str, Path]) -> bool:
        """Check if path is a registered video audio derivative."""
        resolved = str(Path(path).resolve())
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM video_audio_derivatives WHERE derived_path = ? LIMIT 1",
                (resolved,),
            )
            return cursor.fetchone() is not None

    @contextmanager
    def acquire_lock(self, timeout: float = 30.0):
        """Cross-process/thread file lock on the registry database."""
        db_str = str(self.db_path)
        if db_str == ":memory:":
            with self._memory_lock:
                yield
            return

        lock_path = Path(f"{db_str}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            f.close()

    def save_stage_checkpoint(
        self,
        tracking_id: str,
        stage_name: str,
        input_path: Union[str, Path],
        input_sha256: str,
        status: str,
        summary: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Persist or update a stage checkpoint for resumability."""
        now = datetime.now(timezone.utc).isoformat()
        resolved_path = str(Path(input_path).resolve())
        details_json = json.dumps(details) if details is not None else None

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO stage_checkpoints (
                tracking_id, stage_name, input_path, input_sha256,
                status, summary, details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id, stage_name) DO UPDATE SET
                input_path = excluded.input_path,
                input_sha256 = excluded.input_sha256,
                status = excluded.status,
                summary = excluded.summary,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """, (
                tracking_id,
                stage_name,
                resolved_path,
                input_sha256,
                status,
                summary,
                details_json,
                now,
                now,
            ))
            conn.commit()

    def get_stage_checkpoint(
        self,
        tracking_id: str,
        stage_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a specific stage checkpoint by tracking_id and stage_name."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT tracking_id, stage_name, input_path, input_sha256,
                   status, summary, details_json, created_at, updated_at
            FROM stage_checkpoints
            WHERE tracking_id = ? AND stage_name = ?
            """, (tracking_id, stage_name))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("details_json"):
                try:
                    res["details"] = json.loads(res["details_json"])
                except Exception:
                    res["details"] = None
            else:
                res["details"] = None
            return res

    def get_stage_checkpoints(
        self,
        tracking_id: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve all stage checkpoints for a file by tracking_id."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT tracking_id, stage_name, input_path, input_sha256,
                   status, summary, details_json, created_at, updated_at
            FROM stage_checkpoints
            WHERE tracking_id = ?
            ORDER BY created_at ASC
            """, (tracking_id,))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                res = dict(row)
                if res.get("details_json"):
                    try:
                        res["details"] = json.loads(res["details_json"])
                    except Exception:
                        res["details"] = None
                else:
                    res["details"] = None
                results.append(res)
            return results

    def clear_stage_checkpoints(self, tracking_id: Optional[str] = None):
        """Clear stage checkpoints for a specific tracking_id or all."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if tracking_id:
                cursor.execute("DELETE FROM stage_checkpoints WHERE tracking_id = ?", (tracking_id,))
            else:
                cursor.execute("DELETE FROM stage_checkpoints")
            conn.commit()

    def record_scratch_artifact(
        self,
        artifact_path: Union[str, Path],
        run_id: Optional[str] = None,
        tracking_id: Optional[str] = None,
    ) -> None:
        """Record an owned temporary scratch artifact for safe lifecycle tracking."""
        resolved = str(Path(artifact_path).resolve())
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO scratch_artifacts (artifact_path, run_id, tracking_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(artifact_path) DO UPDATE SET
                run_id = excluded.run_id,
                tracking_id = excluded.tracking_id,
                created_at = excluded.created_at
            """, (resolved, run_id, tracking_id, now))
            conn.commit()

    def get_scratch_artifacts(
        self,
        run_id: Optional[str] = None,
        tracking_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve registered scratch artifacts, optionally filtered by run_id or tracking_id."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            query = "SELECT artifact_path, run_id, tracking_id, created_at FROM scratch_artifacts WHERE 1=1"
            params: List[Any] = []
            if run_id:
                query += " AND run_id = ?"
                params.append(run_id)
            if tracking_id:
                query += " AND tracking_id = ?"
                params.append(tracking_id)
            query += " ORDER BY created_at ASC"
            cursor.execute(query, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def remove_scratch_artifact(self, artifact_path: Union[str, Path]) -> None:
        """Remove a scratch artifact record after it has been deleted or cleaned."""
        resolved = str(Path(artifact_path).resolve())
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scratch_artifacts WHERE artifact_path = ?", (resolved,))
            conn.commit()

    def clear_scratch_artifacts(self, run_id: Optional[str] = None) -> None:
        """Clear scratch artifact records for a run or all."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if run_id:
                cursor.execute("DELETE FROM scratch_artifacts WHERE run_id = ?", (run_id,))
            else:
                cursor.execute("DELETE FROM scratch_artifacts")
            conn.commit()

    def record_file_split(self, split_data: Dict[str, Any]) -> int:
        """Record a completed, verified Tool 6 file split."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO file_splits (
                source_tracking_id, source_path, source_sha256, source_duration_seconds,
                cut_point_seconds, singing_tracking_id, singing_path, singing_sha256,
                singing_duration_seconds, singing_leading_silence_seconds, singing_pending_tool_11_move,
                class_tracking_id, class_path, class_sha256, class_duration_seconds,
                class_leading_silence_seconds, class_pending_tool_11_move, tool_version,
                evidence_ids_json, split_details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                split_data["source_tracking_id"],
                split_data["source_path"],
                split_data["source_sha256"],
                float(split_data["source_duration_seconds"]),
                float(split_data["cut_point_seconds"]),
                split_data["singing_tracking_id"],
                split_data["singing_path"],
                split_data["singing_sha256"],
                float(split_data["singing_duration_seconds"]),
                float(split_data.get("singing_leading_silence_seconds", 0.0)),
                1 if split_data.get("singing_pending_tool_11_move", True) else 0,
                split_data["class_tracking_id"],
                split_data["class_path"],
                split_data["class_sha256"],
                float(split_data["class_duration_seconds"]),
                float(split_data.get("class_leading_silence_seconds", 0.0)),
                1 if split_data.get("class_pending_tool_11_move", True) else 0,
                split_data.get("tool_version", "1.0.0"),
                json.dumps(split_data.get("evidence_ids", [])) if isinstance(split_data.get("evidence_ids"), list) else split_data.get("evidence_ids_json"),
                json.dumps(split_data.get("details", {})) if isinstance(split_data.get("details"), dict) else split_data.get("split_details_json"),
                now,
            ))
            row_id = cursor.lastrowid
            conn.commit()
            return row_id

    def get_file_split_by_source(self, source_tracking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve split record for a source tracking ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM file_splits WHERE source_tracking_id = ? ORDER BY id DESC LIMIT 1", (source_tracking_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_file_split_by_child(self, child_tracking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve split record where tracking ID is either singing or class child."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM file_splits WHERE singing_tracking_id = ? OR class_tracking_id = ? ORDER BY id DESC LIMIT 1", (child_tracking_id, child_tracking_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_file_splits(self) -> List[Dict[str, Any]]:
        """List all completed file splits."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM file_splits ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    def save_human_cut_decision(
        self,
        tracking_id: str,
        source_sha256: str,
        cut_point_seconds: float,
        reviewer: str = "human_reviewer",
        notes: Optional[str] = None,
    ) -> None:
        """Persist or update audited human cut decision bound to source SHA-256."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO human_cut_decisions (tracking_id, source_sha256, cut_point_seconds, reviewer, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET
                source_sha256 = excluded.source_sha256,
                cut_point_seconds = excluded.cut_point_seconds,
                reviewer = excluded.reviewer,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """, (tracking_id, source_sha256, float(cut_point_seconds), reviewer, notes, now, now))
            conn.commit()

    def get_human_cut_decision(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve human cut decision for a tracking ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM human_cut_decisions WHERE tracking_id = ?", (tracking_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
