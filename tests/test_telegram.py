from app.models import MarketplaceListing
from app.telegram import format_listing


def test_telegram_html_is_escaped() -> None:
    listing = MarketplaceListing(
        "1",
        '<Mac & "PC">',
        price="<100 & cheap>",
        url='https://www.facebook.com/marketplace/item/1?x="bad"',
    )
    message = format_listing(listing)
    assert '&lt;Mac &amp; "PC"&gt;' in message
    assert "&lt;100 &amp; cheap&gt;" in message
    assert 'href="https://www.facebook.com/marketplace/item/1?x=&quot;bad&quot;"' in message
    assert "Бот впервые обнаружил" not in message
