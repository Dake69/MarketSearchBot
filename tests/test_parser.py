from pathlib import Path

from app.marketplace.parser import listing_id_from_url, parse_html


def test_listing_id_from_url() -> None:
    assert listing_id_from_url("https://facebook.com/marketplace/item/12345/?ref=search") == "12345"
    assert listing_id_from_url("https://facebook.com/marketplace/search") is None


def test_parse_html_and_embedded_json() -> None:
    source = Path("tests/fixtures/marketplace.html").read_text()
    listings = {item.id: item for item in parse_html(source)}
    assert set(listings) == {"123456789", "987654321"}
    assert listings["123456789"].url == "https://www.facebook.com/marketplace/item/123456789"
    assert listings["987654321"].title == "iPhone 16"
    assert listings["987654321"].price == "20 000 Kč"
    assert listings["987654321"].location == "Praha"
