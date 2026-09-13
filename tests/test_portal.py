from fastapi.testclient import TestClient
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
