def test_health_endpoint_returns_research_only_status(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["scope"] == "research-only"



def test_readiness_includes_render_deployed_commit(client, monkeypatch) -> None:
    expected_commit = "a" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", expected_commit)

    response = client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["database"] == "ok"
    assert payload["commit_sha"] == expected_commit
