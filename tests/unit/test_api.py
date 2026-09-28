import pytest
from fastapi.testclient import TestClient

from portfolio_intel.api.app import app
from portfolio_intel.config import settings

AUTH = {"Authorization": f"Bearer {settings.mcp_token}"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health_and_page(client):
    assert client.get("/healthz").json()["ok"] is True
    assert "Portfolio Intelligence" in client.get("/").text


def test_auth_required(client):
    assert client.get("/v1/portfolios").status_code == 401
    assert client.get("/v1/portfolios", headers={"Authorization": "Bearer x"}).status_code == 401


def test_analytics_endpoints(client):
    assert len(client.get("/v1/portfolios", headers=AUTH).json()["portfolios"]) == 5
    e = client.get("/v1/portfolios/EQ_EU_PM/exposures?dimension=industry&region=Europe",
                   headers=AUTH).json()  # fmt: skip
    assert e["benchmark_id"] == "EQ_EU_BMK"
    r = client.get("/v1/portfolios/EQ_EU_PM/risk/contributions?top_n=3", headers=AUTH).json()
    assert len(r["rows"]) == 3
    assert client.get("/v1/portfolios/EQ_EU_PM/risk", headers=AUTH).status_code == 200
    assert client.get("/v1/portfolios/FI_US_PM/duration", headers=AUTH).status_code == 200
    h = client.get("/v1/portfolios/FI_US_PM/holdings?top_n=2", headers=AUTH).json()
    assert len(h["top_holdings"]) == 2
    assert "Euler" in client.get("/v1/methodology", headers=AUTH).json()["markdown"]


def test_simulate_and_errors(client):
    ok = client.post("/v1/portfolios/EQ_EU_PM/simulate", headers=AUTH,
                     json={"trades": [{"security_id": "HSBA.L", "delta_weight_bps": -50}]})  # fmt: skip
    assert ok.status_code == 200 and ok.json()["hypothetical"] is True
    bad = client.post("/v1/portfolios/EQ_EU_PM/simulate", headers=AUTH,
                      json={"trades": [{"security_id": "HSBA.L", "delta_weight_bps": 500}]})  # fmt: skip
    assert bad.status_code == 422
    assert client.post("/v1/portfolios/EQ_EU_PM/simulate", headers=AUTH,
                       json={"trades": []}).status_code == 422  # fmt: skip
    assert client.get("/v1/portfolios/FI_US_PM/risk", headers=AUTH).status_code == 422
    assert client.get("/v1/portfolios/NOPE/risk", headers=AUTH).status_code == 404
