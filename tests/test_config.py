from app.config import Settings


def test_yaml_search_configuration(tmp_path, monkeypatch) -> None:
    config = tmp_path / "queries.yaml"
    config.write_text(
        """searches:
  - name: macs
    query: macbook
    location: Prague
    radius_km: 25
    min_price: 100
    category_id: electronics
"""
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEARCHES_FILE", str(config))
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "60")
    settings = Settings.load(tmp_path / "missing.env")
    assert settings.searches[0].name == "macs"
    assert settings.searches[0].radius_km == 25
    assert settings.searches[0].min_price == 100


def test_rejects_too_frequent_polling(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEARCHES_FILE", "missing.yaml")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "59")
    try:
        Settings.load(tmp_path / "missing.env")
    except ValueError as exc:
        assert "at least 60" in str(exc)
    else:
        raise AssertionError("Expected interval validation")


def test_default_cycle_checks_all_categories_every_five_minutes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEARCHES_FILE", "missing.yaml")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("ALL_CATEGORIES_PER_CYCLE", raising=False)

    settings = Settings.load(tmp_path / "missing.env")

    assert settings.poll_interval_seconds == 300
    assert settings.all_categories_per_cycle == 17
