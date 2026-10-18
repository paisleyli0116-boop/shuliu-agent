from fastapi.testclient import TestClient


def test_health_reports_database_backed_cloud_api_status(cloud_api):
    app, _ = cloud_api

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "HEALTHY", "component": "cloud-api"}
