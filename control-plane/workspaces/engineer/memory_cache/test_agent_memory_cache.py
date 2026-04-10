"""
Pytest tests for AgentMemoryCache.

Covers:
- Insertion (set)
- Retrieval (get)
- TTL expiry
- Capacity eviction (LRU)
"""

import time
from unittest.mock import patch

import pytest

from agent_memory_cache import AgentMemoryCache, CacheEntry


class TestCacheEntry:
    """Tests for the CacheEntry dataclass."""
    
    def test_entry_creation_defaults(self):
        """Entry should have sensible defaults."""
        entry = CacheEntry(key="test", value="data")
        
        assert entry.key == "test"
        assert entry.value == "data"
        assert entry.created_at > 0
        assert entry.accessed_at > 0
        assert entry.ttl_seconds is None
        assert entry.tags == []
    
    def test_entry_not_expired_without_ttl(self):
        """Entry without TTL should never expire."""
        entry = CacheEntry(key="test", value="data", ttl_seconds=None)
        assert not entry.is_expired()
    
    def test_entry_not_expired_within_ttl(self):
        """Entry should not be expired within TTL window."""
        entry = CacheEntry(key="test", value="data", ttl_seconds=3600)
        assert not entry.is_expired()
    
    def test_entry_expired_after_ttl(self):
        """Entry should be expired after TTL passes."""
        entry = CacheEntry(
            key="test",
            value="data",
            created_at=time.time() - 100,
            ttl_seconds=50,
        )
        assert entry.is_expired()
    
    def test_touch_updates_accessed_at(self):
        """touch() should update accessed_at timestamp."""
        entry = CacheEntry(key="test", value="data")
        old_accessed = entry.accessed_at
        
        time.sleep(0.01)  # Small delay
        entry.touch()
        
        assert entry.accessed_at > old_accessed


class TestInsertion:
    """Tests for cache insertion (set)."""
    
    def test_set_basic(self):
        """Basic set should store value."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1")
        
        assert len(cache) == 1
        assert "key1" in cache
    
    def test_set_with_tags(self):
        """Set should store tags correctly."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1", tags=["tag1", "tag2"])
        
        # Tags should be indexed
        assert cache.evict_by_tag("tag1") == 1
    
    def test_set_overwrites_existing(self):
        """Set should overwrite existing key."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1")
        cache.set("key1", "value2")
        
        assert len(cache) == 1
        assert cache.get("key1") == "value2"
    
    def test_set_with_explicit_ttl(self):
        """Set should use explicit TTL over default."""
        cache = AgentMemoryCache(max_size=10, default_ttl=3600)
        cache.set("key1", "value1", ttl=60)
        
        # Can't directly inspect TTL, but entry should exist
        assert cache.get("key1") == "value1"
    
    def test_set_with_none_ttl_never_expires(self):
        """Set with ttl=None should create immortal entry."""
        cache = AgentMemoryCache(max_size=10, default_ttl=1)
        cache.set("key1", "value1", ttl=None)
        
        # Even with tiny default TTL, explicit None should not expire
        time.sleep(0.01)
        assert cache.get("key1") == "value1"


class TestRetrieval:
    """Tests for cache retrieval (get)."""
    
    def test_get_existing_key(self):
        """Get should return stored value."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", {"data": "test"})
        
        result = cache.get("key1")
        assert result == {"data": "test"}
    
    def test_get_missing_key_returns_none(self):
        """Get should return None for missing key."""
        cache = AgentMemoryCache(max_size=10)
        
        assert cache.get("nonexistent") is None
    
    def test_get_missing_key_returns_default(self):
        """Get should return provided default for missing key."""
        cache = AgentMemoryCache(max_size=10)
        
        assert cache.get("nonexistent", default="fallback") == "fallback"
    
    def test_get_updates_access_time(self):
        """Get should update accessed_at for LRU."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        # Access key1 to make it more recent
        cache.get("key1")
        
        # key2 should now be least recently used
        # Add entries to trigger eviction
        cache._max_size = 2  # Shrink for test
        cache.set("key3", "value3")
        
        # key2 should be evicted (LRU), key1 should remain
        assert "key1" in cache
        assert "key2" not in cache
    
    def test_get_tracks_hits_and_misses(self):
        """Get should track hit/miss statistics."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1")
        
        cache.get("key1")  # Hit
        cache.get("key1")  # Hit
        cache.get("missing")  # Miss
        
        stats = cache.stats
        assert stats["hits"] == 2
        assert stats["misses"] == 1


class TestTTLExpiry:
    """Tests for TTL-based expiration."""
    
    def test_expired_entry_returns_none(self):
        """Get should return None for expired entry."""
        cache = AgentMemoryCache(max_size=10, default_ttl=None)
        
        # Manually create expired entry
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            cache.set("key1", "value1", ttl=60)
            
            # Fast forward past TTL
            mock_time.return_value = 1100.0
            result = cache.get("key1")
        
        assert result is None
    
    def test_expired_entry_removed_on_access(self):
        """Expired entry should be removed when accessed."""
        cache = AgentMemoryCache(max_size=10, default_ttl=None)
        
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            cache.set("key1", "value1", ttl=60)
            assert len(cache) == 1
            
            # Fast forward past TTL
            mock_time.return_value = 1100.0
            cache.get("key1")  # Triggers lazy eviction
        
        # Entry should be gone after lazy eviction
        assert len(cache) == 0
    
    def test_gc_removes_expired_entries(self):
        """gc() should remove all expired entries."""
        cache = AgentMemoryCache(max_size=10, default_ttl=None)
        
        with patch('agent_memory_cache.time') as mock_time:
            mock_time.time.return_value = 1000.0
            cache.set("key1", "value1", ttl=60)
            cache.set("key2", "value2", ttl=120)
            cache.set("key3", "value3", ttl=None)  # Never expires
            
            # Fast forward - key1 expired, key2 not yet
            mock_time.time.return_value = 1080.0
            removed = cache.gc()
            
            assert removed == 1
            assert "key1" not in cache
            assert "key2" in cache
            assert "key3" in cache
    
    def test_gc_returns_count(self):
        """gc() should return number of entries removed."""
        cache = AgentMemoryCache(max_size=10, default_ttl=None)
        
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            cache.set("key1", "value1", ttl=10)
            cache.set("key2", "value2", ttl=10)
            cache.set("key3", "value3", ttl=10)
            
            mock_time.return_value = 1100.0
            removed = cache.gc()
        
        assert removed == 3


class TestCapacityEviction:
    """Tests for LRU capacity-based eviction."""
    
    def test_eviction_at_capacity(self):
        """Should evict LRU entry when capacity exceeded."""
        cache = AgentMemoryCache(max_size=3)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # At capacity, add one more
        cache.set("key4", "value4")
        
        assert len(cache) == 3
        assert "key1" not in cache  # LRU, should be evicted
        assert "key4" in cache  # Newest, should exist
    
    def test_access_prevents_eviction(self):
        """Accessing an entry should prevent its eviction."""
        cache = AgentMemoryCache(max_size=3)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # Access key1 to make it recently used
        cache.get("key1")
        
        # Add new entry - key2 should be evicted (now LRU)
        cache.set("key4", "value4")
        
        assert "key1" in cache  # Accessed, should remain
        assert "key2" not in cache  # LRU after key1 access
        assert "key3" in cache
        assert "key4" in cache
    
    def test_eviction_tracks_stats(self):
        """Evictions should be tracked in stats."""
        cache = AgentMemoryCache(max_size=2)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Triggers eviction
        
        assert cache.stats["evictions_lru"] == 1
    
    def test_manual_evict(self):
        """evict() should remove specific entry."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1")
        
        result = cache.evict("key1")
        
        assert result is True
        assert "key1" not in cache
        assert cache.stats["evictions_manual"] == 1
    
    def test_manual_evict_nonexistent(self):
        """evict() should return False for missing key."""
        cache = AgentMemoryCache(max_size=10)
        
        result = cache.evict("nonexistent")
        
        assert result is False
    
    def test_evict_by_tag(self):
        """evict_by_tag() should remove all entries with tag."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1", tags=["group:a"])
        cache.set("key2", "value2", tags=["group:a", "group:b"])
        cache.set("key3", "value3", tags=["group:b"])
        
        removed = cache.evict_by_tag("group:a")
        
        assert removed == 2
        assert "key1" not in cache
        assert "key2" not in cache
        assert "key3" in cache  # Only has group:b


class TestEdgeCases:
    """Edge case and error handling tests."""
    
    def test_invalid_max_size(self):
        """Should reject non-positive max_size."""
        with pytest.raises(ValueError):
            AgentMemoryCache(max_size=0)
        
        with pytest.raises(ValueError):
            AgentMemoryCache(max_size=-1)
    
    def test_clear(self):
        """clear() should remove all entries."""
        cache = AgentMemoryCache(max_size=10)
        cache.set("key1", "value1", tags=["tag1"])
        cache.set("key2", "value2", tags=["tag2"])
        
        cache.clear()
        
        assert len(cache) == 0
        assert cache.evict_by_tag("tag1") == 0  # Tag index also cleared
    
    def test_keys_excludes_expired(self):
        """keys() should not return expired entries."""
        cache = AgentMemoryCache(max_size=10)
        
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            cache.set("key1", "value1", ttl=60)
            cache.set("key2", "value2", ttl=None)
            
            mock_time.return_value = 1100.0
            keys = cache.keys()
        
        assert "key1" not in keys
        assert "key2" in keys
    
    def test_contains_checks_expiry(self):
        """'in' operator should check expiry."""
        cache = AgentMemoryCache(max_size=10)
        
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            cache.set("key1", "value1", ttl=60)
            
            assert "key1" in cache
            
            mock_time.return_value = 1100.0
            assert "key1" not in cache


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
