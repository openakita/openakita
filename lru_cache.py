"""LRU Cache Implementation using OrderedDict."""

from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)


if __name__ == "__main__":
    c = LRUCache(2)
    c.put(1, 1)
    c.put(2, 2)
    assert c.get(1) == 1       # 返回 1
    c.put(3, 3)                # 淘汰 key=2
    assert c.get(2) == -1      # 2 已被淘汰
    c.put(4, 4)                # 淘汰 key=1
    assert c.get(1) == -1      # 1 已被淘汰
    assert c.get(3) == 3
    assert c.get(4) == 4
    print("All tests passed.")
