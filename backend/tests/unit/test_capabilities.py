from __future__ import annotations

import pytest

from app.capabilities import (
    Capabilities,
    PricingEntry,
    load_catalogue,
    validate_request,
)
from app.config import Settings


@pytest.fixture(scope="module")
def catalogue():
    settings = Settings(_env_file=None)
    return load_catalogue(settings.catalogue_path)


def test_catalogue_parses_and_is_non_trivial(catalogue):
    assert len(catalogue.models) >= 20
    assert catalogue.generated_at
    assert catalogue.source.startswith("https://openrouter.ai")


def test_every_model_has_sane_capabilities(catalogue):
    for model in catalogue.models:
        assert model.max_refs >= 0, model.id
        assert model.n["min"] >= 1, model.id
        assert model.n["max"] >= model.n["min"], model.id
        assert model.endpoint_count >= 1, model.id
        assert "image" in model.input_modalities or "text" in model.input_modalities, model.id


def test_at_least_some_models_accept_references(catalogue):
    accepting = [m for m in catalogue.models if m.accepts_references]
    assert len(accepting) > 1


def test_reference_capability_is_per_model(catalogue):
    """The whole UI hinges on this: refs exist as a capability, not a global mode."""
    gemini = catalogue.get("google/gemini-3-pro-image")
    assert gemini is not None
    assert gemini.accepts_references
    assert gemini.max_refs == 14

    no_refs = catalogue.get("recraft/recraft-v4.1-flash")
    assert no_refs is not None
    assert not no_refs.accepts_references


def test_unknown_model_returns_none(catalogue):
    assert catalogue.get("does/not-exist") is None


def _model(**overrides) -> Capabilities:
    base = {
        "id": "test/model",
        "name": "Test",
        "max_refs": 2,
        "n": {"min": 1, "max": 3},
        "resolutions": ["1K", "2K"],
        "aspect_ratios": ["1:1", "16:9"],
        "formats": ["png"],
        "backgrounds": ["auto"],
        "qualities": [],
        "supports_compression": False,
        "supports_seed": True,
        "passthrough_keys": ["steps"],
        "provider_tags": ["acme/global"],
        "endpoint_count": 1,
    }
    base.update(overrides)
    return Capabilities(**base)


def test_valid_request_has_no_errors():
    assert validate_request(_model(), {"n": 2, "aspect_ratio": "16:9", "seed": 7}) == []


def test_rejects_n_outside_range():
    errors = validate_request(_model(), {"n": 4})
    assert any("n must be between" in e for e in errors)


def test_rejects_unsupported_field_instead_of_silently_dropping():
    errors = validate_request(_model(), {"quality": "high"})
    assert any("quality is not supported" in e for e in errors)


def test_rejects_value_not_in_enum():
    errors = validate_request(_model(), {"aspect_ratio": "21:9"})
    assert any("21:9" in e for e in errors)


def test_rejects_too_many_references():
    errors = validate_request(_model(), {"reference_asset_ids": ["a", "b", "c"]})
    assert any("at most 2 reference" in e for e in errors)


def test_rejects_unknown_passthrough_key():
    errors = validate_request(_model(), {"passthrough": {"acme": {"guidance": 3}}})
    assert any("guidance" in e for e in errors)


def test_accepts_allowlisted_passthrough_key():
    assert validate_request(_model(), {"passthrough": {"acme": {"steps": 20}}}) == []


def test_rejects_passthrough_for_unknown_provider():
    errors = validate_request(_model(), {"passthrough": {"nope": {"steps": 1}}})
    assert any("unknown provider" in e for e in errors)


def test_estimate_uses_only_deterministic_pricing():
    model = _model(
        pricing=[
            PricingEntry(billable="output_image", unit="image", cost_usd=0.05),
            PricingEntry(billable="output_image", unit="token", cost_usd=0.0001),
        ]
    )
    assert model.output_cost_per_image() == 0.05

    token_only = _model(
        pricing=[PricingEntry(billable="output_image", unit="token", cost_usd=0.0001)]
    )
    assert token_only.output_cost_per_image() is None


def test_bad_catalogue_fails_loudly(tmp_path):
    path = tmp_path / "models.json"
    path.write_text('{"generated_at": "x", "source": "y", "models": []}')
    with pytest.raises(ValueError, match="empty"):
        load_catalogue(path)


def test_duplicate_model_ids_rejected(tmp_path):
    model = {
        "id": "a/b",
        "name": "A",
        "input_modalities": ["image"],
        "endpoint_count": 1,
    }
    path = tmp_path / "models.json"
    body = ",".join([repr(model).replace("'", '"')] * 2)
    path.write_text(f'{{"generated_at": "x", "source": "y", "models": [{body}]}}')
    with pytest.raises(ValueError, match="duplicate"):
        load_catalogue(path)
