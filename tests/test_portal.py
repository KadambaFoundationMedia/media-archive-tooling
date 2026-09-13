from fastapi.testclient import TestClient
from media_archive_tooling.review_portal.app import app


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
