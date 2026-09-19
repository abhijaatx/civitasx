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
