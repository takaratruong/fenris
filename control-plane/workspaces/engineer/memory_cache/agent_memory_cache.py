"""
Agent Memory Cache - LRU + TTL hybrid cache for agent episodic memory.

This module provides a thread-safe cache implementation optimized for
agent memory use cases where recent and frequently-accessed memories
should be retained while stale memories expire.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class CacheEntry:
    """
    A single entry in the agent memory cache.
    
    Attributes:
        key: Unique identifier for this memory entry
        value: The cached content (any serializable data)
        created_at: Unix timestamp when entry was created
        accessed_at: Unix timestamp of last access (for LRU)
        ttl_seconds: Time-to-live in seconds (None = never expires)
        tags: List of tags for grouping/bulk operations
    """
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    ttl_seconds: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    
    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if this entry has exceeded its TTL."""
        if self.ttl_seconds is None:
            return False
        if now is None:
            now = time.time()
        return now > (self.created_at + self.ttl_seconds)
    
    def touch(self, now: Optional[float] = None) -> None:
        """Update accessed_at timestamp (for LRU tracking)."""
        self.accessed_at = now if now is not None else time.time()


class AgentMemoryCache:
    """
    LRU + TTL hybrid cache for agent episodic memory.
    
    Features:
    - LRU eviction when capacity is exceeded
    - TTL-based expiration with lazy + active garbage collection
    - Tag-based grouping for bulk operations
    - Thread-safe operations
    
    Example:
        cache = AgentMemoryCache(max_size=1000, default_ttl=3600)
        cache.set("user:pref", {"theme": "dark"}, ttl=None, tags=["user"])
        value = cache.get("user:pref")
        cache.evict("user:pref")
        removed_count = cache.gc()
    """
    
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: Optional[int] = 3600,
    ):
        """
        Initialize the cache.
        
        Args:
            max_size: Maximum number of entries before LRU eviction
            default_ttl: Default TTL in seconds for entries without explicit TTL
                        (None = entries never expire by default)
        """
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        
        self._max_size = max_size
        self._default_ttl = default_ttl
        
        # OrderedDict maintains insertion/access order for LRU
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        
        # Tag index for efficient tag-based lookups
        self._tag_index: Dict[str, Set[str]] = {}  # tag -> set of keys
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Stats
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions_lru": 0,
            "evictions_ttl": 0,
            "evictions_manual": 0,
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a value from the cache.
        
        Updates access time for LRU tracking. Returns default if key
        not found or entry has expired.
        
        Args:
            key: The cache key to look up
            default: Value to return if key not found
            
        Returns:
            The cached value, or default if not found/expired
        """
        with self._lock:
            entry = self._entries.get(key)
            
            if entry is None:
                self._stats["misses"] += 1
                return default
            
            # Check TTL expiration (lazy eviction)
            if entry.is_expired():
                self._remove_entry(key, reason="ttl")
                self._stats["misses"] += 1
                return default
            
            # Update access time and move to end (most recently used)
            entry.touch()
            self._entries.move_to_end(key)
            
            self._stats["hits"] += 1
            return entry.value
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = ...,  # Sentinel for "use default"
        tags: Optional[List[str]] = None,
    ) -> None:
        """
        Store a value in the cache.
        
        If key exists, updates the value and resets timestamps.
        Triggers LRU eviction if capacity is exceeded.
        
        Args:
            key: The cache key
            value: The value to cache
            ttl: Time-to-live in seconds (None = never expires,
                 omit to use default_ttl)
            tags: List of tags for grouping
        """
        if tags is None:
            tags = []
        
        # Handle TTL sentinel
        effective_ttl = self._default_ttl if ttl is ... else ttl
        
        now = time.time()
        
        with self._lock:
            # Remove old entry if exists (to update tag index)
            if key in self._entries:
                self._remove_entry(key, reason=None)  # No stat tracking for updates
            
            # Create new entry
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                accessed_at=now,
                ttl_seconds=effective_ttl,
                tags=list(tags),
            )
            
            # Add to entries (at end = most recently used)
            self._entries[key] = entry
            
            # Update tag index
            for tag in tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = set()
                self._tag_index[tag].add(key)
            
            # LRU eviction if over capacity
            while len(self._entries) > self._max_size:
                # Pop from front (least recently used)
                oldest_key = next(iter(self._entries))
                self._remove_entry(oldest_key, reason="lru")
    
    def evict(self, key: str) -> bool:
        """
        Manually remove an entry from the cache.
        
        Args:
            key: The cache key to remove
            
        Returns:
            True if entry was found and removed, False otherwise
        """
        with self._lock:
            if key in self._entries:
                self._remove_entry(key, reason="manual")
                return True
            return False
    
    def evict_by_tag(self, tag: str) -> int:
        """
        Remove all entries with a specific tag.
        
        Args:
            tag: The tag to match
            
        Returns:
            Number of entries removed
        """
        with self._lock:
            keys = list(self._tag_index.get(tag, []))
            for key in keys:
                self._remove_entry(key, reason="manual")
            return len(keys)
    
    def gc(self) -> int:
        """
        Garbage collect expired entries.
        
        Scans all entries and removes those that have exceeded their TTL.
        Call periodically for proactive cleanup.
        
        Returns:
            Number of entries removed
        """
        now = time.time()
        removed = 0
        
        with self._lock:
            # Collect expired keys (can't modify dict during iteration)
            expired_keys = [
                key for key, entry in self._entries.items()
                if entry.is_expired(now)
            ]
            
            for key in expired_keys:
                self._remove_entry(key, reason="ttl")
                removed += 1
        
        return removed
    
    def _remove_entry(self, key: str, reason: Optional[str]) -> None:
        """
        Internal method to remove an entry and update indexes.
        
        Args:
            key: The key to remove
            reason: "lru", "ttl", "manual", or None (no stat tracking)
        """
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        
        # Update tag index
        for tag in entry.tags:
            if tag in self._tag_index:
                self._tag_index[tag].discard(key)
                if not self._tag_index[tag]:
                    del self._tag_index[tag]
        
        # Update stats
        if reason == "lru":
            self._stats["evictions_lru"] += 1
        elif reason == "ttl":
            self._stats["evictions_ttl"] += 1
        elif reason == "manual":
            self._stats["evictions_manual"] += 1
    
    def __len__(self) -> int:
        """Return the number of entries in the cache."""
        with self._lock:
            return len(self._entries)
    
    def __contains__(self, key: str) -> bool:
        """Check if a key exists and is not expired (without touching)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            return not entry.is_expired()
    
    def keys(self) -> List[str]:
        """Return list of all non-expired keys."""
        with self._lock:
            now = time.time()
            return [k for k, e in self._entries.items() if not e.is_expired(now)]
    
    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._entries.clear()
            self._tag_index.clear()
    
    @property
    def stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        with self._lock:
            return dict(self._stats)
    
    @property
    def max_size(self) -> int:
        """Return the maximum cache size."""
        return self._max_size
