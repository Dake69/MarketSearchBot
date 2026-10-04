import pytest

from app.marketplace.search import build_search_url, location_slug
from app.models import SearchConfig


def test_build_search_url_keeps_filters_isolated() -> None:
    search = SearchConfig(
        name="macs",
        query="macbook pro",
        location="München",
        radius_km=25,
        min_price=100,
        max_price=900,
        category_id="electronics",
    )
    url = build_search_url(search)
    assert "/marketplace/munchen/search?" in url
    assert "query=macbook+pro" in url
    assert "sortBy=creation_time_descend" in url
    assert "minPrice=100" in url and "maxPrice=900" in url
    assert location_slug("České Budějovice") == "ceske-budejovice"


def test_empty_query_and_category_cannot_silently_use_ranked_root() -> None:
    with pytest.raises(ValueError, match="Today's picks"):
        build_search_url(
            SearchConfig(name="all", location="107645375935528", query="", category_id=None)
        )
