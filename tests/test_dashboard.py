from urllib.parse import parse_qs, urlparse

from tests.conftest import ALICE_TOKEN, BOB_TOKEN, confirmed


def test_dashboard_assets_are_public_but_session_is_authenticated(cloud):
    client = cloud.client
    client.headers.pop("Authorization")
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="file-input"' in response.text
    assert ALICE_TOKEN not in response.text
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    for asset, mime in [("app.js", "javascript"), ("styles.css", "text/css")]:
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200
        assert mime in response.headers["content-type"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/api/session").status_code == 401
    session = client.get("/api/session", headers={"Authorization": f"Bearer {BOB_TOKEN}"})
    assert session.json() == {
        "user_id": "bob",
        "region": cloud.settings.aws_region,
        "max_upload_bytes": cloud.settings.max_upload_bytes,
    }
    assert ALICE_TOKEN not in session.text and BOB_TOKEN not in session.text


def test_downloads_are_attachments_and_processing_details_are_exposed(cloud):
    file = confirmed(cloud)
    assert file["attempt_count"] == 0
    assert file["last_error"] is None
    assert file["has_thumbnail"] is False
    response = cloud.client.get(f"/api/files/{file['file_id']}/download")
    query = parse_qs(urlparse(response.json()["download_url"]).query)
    assert query["response-content-disposition"] == ["attachment; filename*=UTF-8''notes.pdf"]
    assert query["response-content-type"] == ["application/octet-stream"]
    thumbnail = cloud.client.get(f"/api/files/{file['file_id']}/download?variant=thumbnail")
    assert thumbnail.status_code == 409
    assert (
        cloud.client.get(
            f"/api/files/{file['file_id']}/download?variant=thumbnail",
            headers={"Authorization": f"Bearer {BOB_TOKEN}"},
        ).status_code
        == 404
    )
