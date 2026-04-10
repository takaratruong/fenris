# Cache Entry Schema Design

## CacheEntry Structure

```python
from dataclasses import dataclass, field
from typing import Any, List, Optional
import time

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
    
    def is_expired(self) -> bool:
        """Check if this entry has exceeded its TTL."""
        if self.ttl_seconds is None:
            return False
        return time.time() > (self.created_at + self.ttl_seconds)
    
    def touch(self) -> None:
        """Update accessed_at timestamp (for LRU tracking)."""
        self.accessed_at = time.time()
```

## Field Specifications

| Field | Type | Required | Default | Purpose |
|-------|------|----------|---------|---------|
| `key` | `str` | Yes | - | Unique lookup identifier |
| `value` | `Any` | Yes | - | The cached memory content |
| `created_at` | `float` | No | `time.time()` | Creation timestamp for TTL calculation |
| `accessed_at` | `float` | No | `time.time()` | Last access timestamp for LRU ordering |
| `ttl_seconds` | `Optional[int]` | No | `None` | Expiration duration (None = immortal) |
| `tags` | `List[str]` | No | `[]` | Semantic grouping labels |

## Design Decisions

1. **`key` as string**: Universal, hashable, supports namespacing (e.g., "conv:123:msg:456")

2. **`value` as Any**: Agent memories vary (text, embeddings, metadata dicts) - flexibility required

3. **Timestamps as float**: Unix timestamps for simplicity and timezone independence

4. **`ttl_seconds` as Optional[int]**: 
   - Integer for clarity (no sub-second precision needed for memories)
   - Optional allows permanent entries when desired

5. **`tags` as List[str]**: 
   - Enables bulk operations: `evict_by_tag("conversation:old-session")`
   - Multiple tags allow cross-cutting concerns (e.g., ["conversation:123", "tool:browser", "high-priority"])

## Example Entries

```python
# Short-term tool result
CacheEntry(
    key="tool:browser:result:abc123",
    value={"url": "https://...", "content": "..."},
    ttl_seconds=3600,  # 1 hour
    tags=["tool:browser", "session:current"]
)

# Long-term user preference
CacheEntry(
    key="user:pref:timezone",
    value="America/New_York",
    ttl_seconds=None,  # Never expires
    tags=["user:preference"]
)

# Episodic conversation memory
CacheEntry(
    key="conv:thr_123:summary",
    value="User asked about caching strategies...",
    ttl_seconds=86400,  # 24 hours
    tags=["conversation:thr_123", "type:summary"]
)
```

---
*Schema designed: 2026-04-10*
