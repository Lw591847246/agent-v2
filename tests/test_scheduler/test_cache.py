from app.scheduler.cache import IntentCache


def test_set_get():
    c = IntentCache(max_size=2, ttl=60)
    c.set("k1", {"v": 1})
    assert c.get("k1") == {"v": 1}


def test_lru_eviction():
    c = IntentCache(max_size=2, ttl=60)
    c.set("k1", 1)
    c.set("k2", 2)
    c.set("k3", 3)
    assert c.get("k1") is None
    assert c.get("k2") == 2
    assert c.get("k3") == 3


def test_ttl_expiry():
    import time
    c = IntentCache(max_size=2, ttl=0)
    c.set("k1", 1)
    time.sleep(0.01)
    assert c.get("k1") is None