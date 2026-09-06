from pathlib import Path


def test_api_location_precedes_static_asset_regex() -> None:
    config = (Path(__file__).parents[2] / "frontend" / "nginx.conf").read_text()

    assert "location ^~ /api/" in config


def test_article_routes_allow_xhs_worker_result_timeout() -> None:
    config = (Path(__file__).parents[2] / "frontend" / "nginx.conf").read_text()
    article_location = config.split("location ^~ /api/v1/articles/ {", 1)[1].split(
        "}", 1
    )[0]

    assert "proxy_read_timeout 310s;" in article_location
