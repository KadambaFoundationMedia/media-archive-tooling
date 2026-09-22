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
