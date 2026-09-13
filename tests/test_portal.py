from fastapi.testclient import TestClient
import media_archive_tooling.review_portal.app as portal_app
from media_archive_tooling.review_portal.app import app, _dashboard_record


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


def test_dashboard_hides_tracking_token_from_proposed_filename():
    record = {
        "original_path": "/tmp/sample-files/2015/example.mp3",
        "current_filename": "example.mp3",
        "proposed_filename": "2015-07-25_KKS_SB-7-2-16_Radhadesh-be_ID-deadbeef.mp3",
    }
    display = _dashboard_record(record)
    assert display["display_proposed_filename"] == "2015-07-25_KKS_SB-7-2-16_Radhadesh-be.mp3"
    assert "deadbeef" not in display["display_proposed_filename"]


def test_portal_file_detail_and_update():
    from media_archive_tooling.review_portal.app import get_registry
    client = TestClient(app)
    reg = get_registry()
    files = reg.list_files()
    if files:
        f = files[0]
        tid = f["tracking_id"]
        res = client.get(f"/file/{tid}")
        assert res.status_code == 200

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
