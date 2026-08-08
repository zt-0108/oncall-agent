from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.observability.metrics import MetricsMiddleware


def test_metrics_middleware_records_route_template_and_status() -> None:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    response = TestClient(app).get("/items/example")
    metrics = generate_latest().decode("utf-8")

    assert response.status_code == 200
    expected = 'oncall_agent_http_requests_total{method="GET",path="/items/{item_id}",status="200"}'
    assert expected in metrics
    assert 'path="/items/{item_id}"' in metrics


def test_metrics_middleware_avoids_unmatched_path_cardinality() -> None:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    response = TestClient(app).get("/arbitrary/not-found/value")
    metrics = generate_latest().decode("utf-8")

    assert response.status_code == 404
    assert 'path="__unmatched__",status="404"' in metrics
    assert "/arbitrary/not-found/value" not in metrics
