from __future__ import annotations

from app.providers.base import ProviderRequest
from app.providers.fake import _png
from app.providers.openrouter import build_request_payload

PNG = b"\x89PNG\r\n\x1a\nsmall"


def _request(**params) -> ProviderRequest:
    return ProviderRequest(
        model="google/gemini-3-pro-image",
        prompt="a red panda astronaut",
        params=params,
        session_id="job-123",
    )


def test_minimal_payload_matches_openrouter_schema():
    """Asserts the exact body we send. This is the test that catches a rename upstream."""
    assert build_request_payload(_request(n=1)) == {
        "model": "google/gemini-3-pro-image",
        "prompt": "a red panda astronaut",
        "n": 1,
        "session_id": "job-123",
    }


def test_none_fields_are_omitted_not_sent_as_null():
    payload = build_request_payload(_request(n=1, resolution=None, aspect_ratio="16:9"))
    assert "resolution" not in payload
    assert payload["aspect_ratio"] == "16:9"
    assert None not in payload.values()


def test_all_translatable_fields_are_forwarded():
    payload = build_request_payload(
        _request(
            n=2,
            resolution="2K",
            aspect_ratio="3:2",
            output_format="webp",
            background="transparent",
            quality="high",
            output_compression=80,
            seed=42,
        )
    )
    assert payload["resolution"] == "2K"
    assert payload["aspect_ratio"] == "3:2"
    assert payload["output_format"] == "webp"
    assert payload["background"] == "transparent"
    assert payload["quality"] == "high"
    assert payload["output_compression"] == 80
    assert payload["seed"] == 42


def test_references_become_base64_data_urls():
    request = _request(n=1, reference_asset_ids=["a"])
    request.references = [(PNG, "image/png")]
    payload = build_request_payload(request)
    assert payload["input_references"] == ["data:image/png;base64,iVBORw0KGgpzbWFsbA=="]
    # The asset ids are our internal ids and must not leak to the provider.
    assert "reference_asset_ids" not in payload


def test_passthrough_is_nested_under_provider_options():
    payload = build_request_payload(
        _request(n=1, passthrough={"black-forest-labs": {"steps": 40}, "empty": {}})
    )
    assert payload["provider"] == {"options": {"black-forest-labs": {"steps": 40}}}


def test_internal_only_params_never_leak():
    payload = build_request_payload(_request(n=1, reference_asset_ids=["a", "b"]))
    assert set(payload) <= {
        "model",
        "prompt",
        "n",
        "session_id",
        "aspect_ratio",
        "background",
        "output_compression",
        "output_format",
        "quality",
        "resolution",
        "seed",
        "input_references",
        "provider",
    }


def test_fake_images_are_deterministic():
    assert _png("same prompt", 0) == _png("same prompt", 0)
    assert _png("same prompt", 0) != _png("same prompt", 1)
    assert _png("a", 0) != _png("b", 0)
