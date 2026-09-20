from __future__ import annotations

from pathlib import Path

from civitas_api.live_connectors import LiveConnectorRegistry
from civitas_api.research import ResearchIndex


def test_live_connector_rejects_redirects_before_reading_the_body(
    tmp_path: Path, monkeypatch
):
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
    registry = LiveConnectorRegistry(status_path=tmp_path / "status.json")
    captured: dict[str, object] = {}

    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data/"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            del args, kwargs
            return RedirectResponse()

    monkeypatch.setattr("civitas_api.live_connectors.httpx.Client", FakeClient)
    result = registry.refresh("bengaluru-open-data-gba", index)

    assert captured["follow_redirects"] is False
    assert result.endpoint.status == "offline"
    assert "redirects are disabled" in result.message.casefold()


def test_live_connector_follows_only_allowlisted_redirects(
    tmp_path: Path, monkeypatch
):
    index = ResearchIndex(
        Path(__file__).parents[1] / "data" / "corpus" / "manifest.json",
        live_cache_path=tmp_path / "live.json",
    )
    registry = LiveConnectorRegistry(status_path=tmp_path / "status.json")
    responses = [
        {
            "status_code": 301,
            "headers": {"location": "https://majestic.bmtc.co.in/"},
            "body": b"",
            "content_type": "text/html",
        },
        {
            "status_code": 200,
            "headers": {},
            "body": b"<html><title>BMTC</title><body>Routes</body></html>",
            "content_type": "text/html",
        },
    ]
    calls: list[str] = []

    class Response:
        def __init__(self, item: dict[str, object]):
            self.status_code = int(item["status_code"])
            self.headers = item["headers"]
            self.body = item["body"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.body

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url):
            assert method == "GET"
            calls.append(url)
            item = responses.pop(0)
            return Response(item)

    monkeypatch.setattr("civitas_api.live_connectors.httpx.Client", FakeClient)
    result = registry.refresh("bmtc-transit", index)

    assert result.fetched is True
    assert result.endpoint.status == "ready"
    assert calls == ["https://bmtc.co.in/", "https://majestic.bmtc.co.in/"]
