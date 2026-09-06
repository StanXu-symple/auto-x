from pathlib import Path


def test_api_location_precedes_static_asset_regex() -> None:
    config = (Path(__file__).parents[2] / "frontend" / "nginx.conf").read_text()

    assert "location ^~ /api/" in config
