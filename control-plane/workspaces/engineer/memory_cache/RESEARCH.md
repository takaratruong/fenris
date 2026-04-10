# Caching Strategies Research

## 1. LRU (Least Recently Used)

**Mechanism**: Evicts the entry that hasn't been accessed for the longest time.

**Pros**:
- Simple to implement (doubly-linked list + hashmap)
- O(1) get/set operations
- Good for temporal locality patterns
- Works well when recent memories are more relevant

**Cons**:
- Doesn't consider access frequency
- A rarely-used item accessed once gets promoted over frequently-used items
- No inherent expiration mechanism

**Use case fit for agent memory**: 
Strong fit. Agent episodic memory naturally has temporal locality - recent interactions are typically more relevant than old ones.

---

## 2. LFU (Least Frequently Used)

**Mechanism**: Evicts entries with the lowest access count.

**Pros**:
- Keeps frequently accessed entries longer
- Good for stable, repeatedly-accessed data

**Cons**:
- O(log n) operations with naive implementation
- "Cache pollution" problem: old popular items block new items
- Requires frequency counters (more memory overhead)
- Cold start problem for new entries

**Use case fit for agent memory**:
Weak fit. Agent memories don't typically have stable "popular" items - importance decays over time, not by access frequency.

---

## 3. TTL (Time-To-Live)

**Mechanism**: Entries expire after a configured duration.

**Pros**:
- Automatic staleness handling
- Simple conceptual model
- Ensures eventual cleanup of unused entries
- Can be combined with other strategies

**Cons**:
- Doesn't respond to access patterns
- Requires periodic garbage collection or lazy expiration
- Fixed TTL may not suit variable-importance data

**Use case fit for agent memory**:
Essential addition. Agent memories should expire (episodic memories fade). Works best combined with capacity-based eviction.

---

## 4. Hybrid: LRU + TTL (Selected Approach)

**Rationale**: 
- LRU handles capacity limits naturally via access recency
- TTL ensures stale memories don't persist indefinitely
- Tags enable semantic grouping for bulk operations
- Together they model how human episodic memory works: recent/accessed memories stay, old unused ones fade

**Implementation Strategy**:
1. Primary eviction: LRU when capacity exceeded
2. Secondary expiration: TTL-based garbage collection
3. Tags: Enable domain-specific eviction (e.g., evict all "conversation:123" entries)

---

## Selection Justification

For **agent episodic memory**, I select **LRU + TTL hybrid** because:

1. **Temporal relevance**: Recent agent interactions are more likely to be contextually relevant
2. **Graceful decay**: TTL ensures memories don't persist forever when unused
3. **Implementation simplicity**: LRU is O(1) and well-understood
4. **Memory efficiency**: No frequency counters needed (unlike LFU)
5. **Tag support**: Enables semantic grouping for conversation/session-based eviction

---
*Research completed: 2026-04-10*
