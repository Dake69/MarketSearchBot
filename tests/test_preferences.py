import pytest

from app.marketplace.search import build_search_url
from app.models import SearchConfig
from app.preferences import ALL_CATEGORY_IDS, PreferencesStore, normalize_location


@pytest.mark.asyncio
async def test_multiple_queries_and_categories_expand_to_combinations(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.db")
    await store.open(SearchConfig(name="default", query="macbook", location="prague"))
    try:
        await store.add_queries(["iphone", "macbook"])
        await store.clear_categories()
        await store.toggle_category("electronics")
        await store.toggle_category("classifieds")
        preferences = await store.get()
        searches = preferences.searches()
        assert preferences.combination_count == 4
        assert {(item.query, item.category_id) for item in searches} == {
            ("macbook", "electronics"),
            ("macbook", "classifieds"),
            ("iphone", "electronics"),
            ("iphone", "classifieds"),
        }
        assert len({item.name for item in searches}) == 4
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_preferences_survive_reopen(tmp_path) -> None:
    path = tmp_path / "preferences.db"
    defaults = SearchConfig(name="default", query="macbook", location="prague")
    first = PreferencesStore(path)
    await first.open(defaults)
    await first.set_location("107645375935528")
    await first.set_radius(75)
    await first.set_price("max_price", 25000)
    await first.clear_queries()
    await first.close()

    second = PreferencesStore(path)
    await second.open(defaults)
    try:
        preferences = await second.get()
        assert preferences.location == "107645375935528"
        assert preferences.radius_km == 75
        assert preferences.max_price == 25000
        assert preferences.queries == ()
    finally:
        await second.close()


def test_location_can_be_extracted_from_marketplace_url() -> None:
    assert (
        normalize_location("https://www.facebook.com/marketplace/107645375935528/search?query=x")
        == "107645375935528"
    )
    with pytest.raises(ValueError):
        normalize_location("https://www.facebook.com/marketplace/search")


@pytest.mark.asyncio
async def test_all_listings_can_expand_to_newest_category_searches(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.db")
    await store.open(SearchConfig(name="default", query="", location="brno"))
    try:
        preferences = await store.get()
        searches = preferences.searches(ALL_CATEGORY_IDS[:5])
        assert len(searches) == 5
        assert all(item.category_id for item in searches)
        assert all("sortBy=creation_time_descend" in build_search_url(item) for item in searches)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_all_categories_are_selected_by_default(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.db")
    await store.open(SearchConfig(name="default", query="", location="brno"))
    try:
        preferences = await store.get()
        assert set(preferences.categories) == set(ALL_CATEGORY_IDS)
        assert len(preferences.searches()) == len(ALL_CATEGORY_IDS)
    finally:
        await store.close()
