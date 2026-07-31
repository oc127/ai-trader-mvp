from src.config import _deep_merge, load_config


def test_deep_merge():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}, "e": 5}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3
    assert result["e"] == 5


def test_load_config():
    cfg = load_config("testnet")
    assert cfg["exchange"]["use_testnet"] is True
    assert "strategy" in cfg
    assert "risk" in cfg
