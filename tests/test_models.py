from app.models import MarketplaceListing


def test_listing_id_has_priority() -> None:
    listing = MarketplaceListing("42", "Test", price="10", url="https://example.test")
    assert listing.id == "42"


def test_fingerprint_is_stable_and_sensitive() -> None:
    first = MarketplaceListing(None, " MacBook ", price="10", location="Prague", url="/item/x")
    same = MarketplaceListing(None, "macbook", price="10", location="prague", url="/ITEM/X")
    changed = MarketplaceListing(None, "macbook", price="11", location="prague", url="/item/x")
    assert first.id.startswith("fp:")
    assert first.id == same.id
    assert first.id != changed.id
