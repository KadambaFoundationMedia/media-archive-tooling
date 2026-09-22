from pathlib import Path
import os
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import media_archive_tooling.review_portal.app as portal_app
from media_archive_tooling.review_portal.app import app, _dashboard_record, configure_review_context
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.models import (
    Context,
    Identity,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
    WhatResult,
    WhenResult,
    WhereResult,
)


def test_portal_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_portal_dashboard():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Media Archive Tooling — Review Portal" in response.text
    assert 'id="theme-toggle"' in response.text
    assert 'id="select-all"' in response.text
    assert 'id="batch-form"' in response.text
    assert 'id="batch-approve"' in response.text
    assert 'id="batch-defer"' in response.text
    assert 'id="batch-commit"' in response.text
    assert 'id="batch-approve-commit"' in response.text
    assert "Approve selected" in response.text
    assert "Defer selected" in response.text
    assert "Commit selected" in response.text
    assert "Approve + commit selected" in response.text
    assert "Original filename" in response.text
    assert "Proposed filename" in response.text
    assert "<th>Path</th>" in response.text
    assert "<th>ID</th>" not in response.text


def test_portal_recovers_when_registry_was_deleted_while_running(tmp_path):
    registry_path = tmp_path / "deleted-registry.db"
    registry = LocalRegistry(registry_path)
    os.unlink(registry_path)

    # Simulates an already-running portal whose next SQLite connection creates
    # a new empty file without the schema.
    assert registry.list_files() == []


def test_dashboard_hides_tracking_token_from_proposed_filename():
    record = {
        "original_path": "/tmp/sample-files/2015/example.mp3",
        "current_filename": "example.mp3",
        "proposed_filename": "2015-07-25_KKS_SB-7-2-16_Radhadesh-be_ID-deadbeef.mp3",
    }
    display = _dashboard_record(record)
    assert display["display_proposed_filename"] == "2015-07-25_KKS_SB-7-2-16_Radhadesh-be.mp3"
    assert "deadbeef" not in display["display_proposed_filename"]


def test_portal_file_detail_and_update(tmp_path):
    reg = LocalRegistry(tmp_path / "portal_detail.db")
    source = tmp_path / "detail_media.mp3"
    source.write_bytes(b"detail media audio")
    proposal = make_portal_proposal(source, "detail01", RenameMode.INITIAL, needs_review=True)
    reg.save_proposal(proposal)
    configure_review_context(
        registry=reg,
        media_db_updater_service=MagicMock(),
        review_root=tmp_path,
    )

    client = TestClient(app)
    files = reg.list_files()
    assert len(files) == 1
    f = files[0]
    tid = f["tracking_id"]
    res = client.get(f"/file/{tid}")
    assert res.status_code == 200
    assert 'id="theme-toggle"' in res.text
    assert "media-archive-theme" in res.text
    assert 'data-theme="dark"' in res.text

    res_post = client.post(f"/file/{tid}/update", data={
        "action": "approve",
        "when_val": f["when_val"],
        "what_val": f["what_val"],
        "where_val": f["where_val"],
        "proposed_filename": f["proposed_filename"]
    }, follow_redirects=True)
    assert res_post.status_code == 200
    updated = reg.get_file(tid)
    assert updated["status"] == "approved"


def test_batch_approve_uses_application_service(monkeypatch):
    calls = []

    class DummyService:
        def get_file(self, tracking_id):
            if tracking_id in {"aaa11111", "bbb22222"}:
                return {"tracking_id": tracking_id, "needs_review": True, "original_filename": f"{tracking_id}.mp3"}
            return None

        def apply_review_action(self, **kwargs):
            calls.append(kwargs)
            return {"tracking_id": kwargs["tracking_id"], "status": "approved"}

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={
            "tracking_ids": ["aaa11111", "bbb22222"],
            "action": "approve",
            "filter": "review",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "filter=review" in response.headers["location"]
    assert [call["tracking_id"] for call in calls] == ["aaa11111", "bbb22222"]
    assert all(call["action"] == "approve" for call in calls)
    assert all(call["reviewer"] == "review_portal_batch" for call in calls)


def test_batch_commit_uses_commit_service(monkeypatch):
    commit_calls = []

    class DummyService:
        def get_file(self, tracking_id):
            return {
                "tracking_id": tracking_id,
                "needs_review": False,
                "status": "approved",
                "original_filename": "ready.mp3",
            }

    class DummyCommitService:
        def commit_file(self, **kwargs):
            commit_calls.append(kwargs)
            return {"tracking_id": kwargs["tracking_id"], "status": "committed"}

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    monkeypatch.setattr(portal_app, "get_commit_service", lambda: DummyCommitService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["aaa11111"], "action": "commit", "filter": "all"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert commit_calls == [{"tracking_id": "aaa11111", "reviewer": "review_portal_batch"}]
    assert "committed" in response.headers["location"]


def test_batch_approve_commit_approves_before_commit(monkeypatch):
    events = []

    class DummyService:
        def get_file(self, tracking_id):
            return {
                "tracking_id": tracking_id,
                "needs_review": True,
                "status": "pending",
                "original_filename": "review.mp3",
            }

        def apply_review_action(self, **kwargs):
            events.append(("approve", kwargs["tracking_id"]))
            return {"tracking_id": kwargs["tracking_id"], "status": "approved"}

    class DummyCommitService:
        def commit_file(self, **kwargs):
            events.append(("commit", kwargs["tracking_id"]))
            return {"tracking_id": kwargs["tracking_id"], "status": "committed"}

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    monkeypatch.setattr(portal_app, "get_commit_service", lambda: DummyCommitService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["review01"], "action": "approve_commit", "filter": "review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert events == [("approve", "review01"), ("commit", "review01")]


def test_batch_review_action_rejects_non_review_rows(monkeypatch):
    class DummyService:
        def get_file(self, tracking_id):
            return {"tracking_id": tracking_id, "needs_review": False, "status": "pending"}

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["safe0001"], "action": "defer", "filter": "all"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "currently requiring human review" in response.text


def test_batch_commit_rejects_unresolved_review_rows(monkeypatch):
    class DummyService:
        def get_file(self, tracking_id):
            return {"tracking_id": tracking_id, "needs_review": True, "status": "pending"}

    class DummyCommitService:
        def commit_file(self, **kwargs):
            raise AssertionError("commit must not run for unresolved review rows")

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    monkeypatch.setattr(portal_app, "get_commit_service", lambda: DummyCommitService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["review01"], "action": "commit", "filter": "review"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "review blockers resolved" in response.text


def test_batch_action_rejects_unsupported_action(monkeypatch):
    class DummyService:
        def get_file(self, tracking_id):
            return {"tracking_id": tracking_id, "needs_review": True}

    monkeypatch.setattr(portal_app, "get_service", lambda: DummyService())
    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["review01"], "action": "delete", "filter": "review"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "approve, defer, commit, or approve_commit" in response.text


def make_portal_proposal(source: Path, tracking_id: str, mode: RenameMode, *, needs_review: bool = False) -> RenameProposal:
    parser = ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=source.name,
            original_path=str(source),
            current_filename=source.name,
            extension=source.suffix.lower(),
        ),
        context=Context(parent_folder=source.parent.name),
        when=WhenResult(selected_value="2015-07-25", precision="day", state=ResolutionState.EXACT),
        what=WhatResult(selected_value="SB-7-2-16", state=ResolutionState.EXACT),
        where=WhereResult(
            place_location="Radhadesh",
            country="Belgium",
            country_iso2="be",
            state=ResolutionState.EXACT,
        ),
        review_reasons=["manual check"] if needs_review else [],
    )
    proposed_filename = f"2015-07-25_KKS_SB-7-2-16_Radhadesh-be_ID-{tracking_id}.mp3"
    return RenameProposal(
        tracking_id=tracking_id,
        original_path=str(source),
        current_filename=source.name,
        proposed_filename=proposed_filename,
        proposed_path=str(source.with_name(proposed_filename)),
        mode=mode,
        needs_review=needs_review,
        review_reasons=list(parser.review_reasons),
        changes_detected=True,
        parser_result=parser,
    )


def test_portal_commit_initial_proposal_does_not_call_tool_4(tmp_path):
    reg_path = tmp_path / "portal_init.db"
    reg = LocalRegistry(reg_path)
    source = tmp_path / "initial_media.mp3"
    source.write_bytes(b"initial media audio")

    prop = make_portal_proposal(source, "trk_portal_init", RenameMode.INITIAL)
    reg.save_proposal(prop)
    reg.update_status("trk_portal_init", "approved")

    mock_updater = MagicMock()
    configure_review_context(registry=reg, media_db_updater_service=mock_updater, review_root=tmp_path)

    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["trk_portal_init"], "action": "commit", "filter": "all"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert mock_updater.synchronize.call_count == 0
    assert reg.get_media_db_sync("trk_portal_init") is None
    rec = reg.get_file("trk_portal_init")
    assert rec["status"] == "committed"
    assert rec["proposal_mode"] == "initial"


def test_portal_commit_enrich_proposal_does_not_call_tool_4(tmp_path):
    reg_path = tmp_path / "portal_enrich.db"
    reg = LocalRegistry(reg_path)
    source = tmp_path / "enrich_media.mp3"
    source.write_bytes(b"enrich media audio")

    prop = make_portal_proposal(source, "trk_portal_enrich", RenameMode.ENRICH)
    reg.save_proposal(prop)
    reg.update_status("trk_portal_enrich", "approved")

    mock_updater = MagicMock()
    configure_review_context(registry=reg, media_db_updater_service=mock_updater, review_root=tmp_path)

    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["trk_portal_enrich"], "action": "commit", "filter": "all"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert mock_updater.synchronize.call_count == 0
    assert reg.get_media_db_sync("trk_portal_enrich") is None
    rec = reg.get_file("trk_portal_enrich")
    assert rec["status"] == "committed"
    assert rec["proposal_mode"] == "enrich"


def test_portal_commit_finalize_proposal_calls_tool_4(tmp_path):
    reg_path = tmp_path / "portal_fin.db"
    reg = LocalRegistry(reg_path)
    source = tmp_path / "finalize_media.mp3"
    source.write_bytes(b"finalize media audio")

    prop = make_portal_proposal(source, "trk_portal_fin", RenameMode.FINALIZE)
    reg.save_proposal(prop)
    reg.update_status("trk_portal_fin", "approved")

    mock_updater = MagicMock()
    configure_review_context(registry=reg, media_db_updater_service=mock_updater, review_root=tmp_path)

    client = TestClient(app)
    response = client.post(
        "/batch/update",
        data={"tracking_ids": ["trk_portal_fin"], "action": "commit", "filter": "all"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert mock_updater.synchronize.call_count == 1
    assert reg.get_media_db_sync("trk_portal_fin") is not None
    rec = reg.get_file("trk_portal_fin")
    assert rec["status"] == "committed"
    assert rec["proposal_mode"] == "finalize"
