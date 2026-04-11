#!/usr/bin/env python3
"""
CAP Theorem Demonstration: Network Partition Simulation

This script demonstrates the CAP theorem trade-off by simulating
two nodes that experience a network partition and showing how
CP (Consistency-Partition tolerance) and AP (Availability-Partition tolerance)
systems handle the situation differently.

Generated for task: tsk_1777c1bc3061
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SystemType(Enum):
    CP = "Consistency-Partition tolerance"
    AP = "Availability-Partition tolerance"


@dataclass
class Node:
    """Represents a node in a distributed system."""
    name: str
    value: int = 0
    last_sync: float = field(default_factory=time.time)
    
    def write(self, new_value: int) -> None:
        self.value = new_value
        self.last_sync = time.time()


@dataclass
class DistributedSystem:
    """Simulates a two-node distributed system."""
    system_type: SystemType
    node_a: Node = field(default_factory=lambda: Node("Node-A"))
    node_b: Node = field(default_factory=lambda: Node("Node-B"))
    partitioned: bool = False
    
    def create_partition(self) -> None:
        """Simulate a network partition between nodes."""
        self.partitioned = True
        print(f"⚡ NETWORK PARTITION CREATED between {self.node_a.name} and {self.node_b.name}")
    
    def heal_partition(self) -> None:
        """Heal the network partition."""
        self.partitioned = False
        print(f"✅ PARTITION HEALED - nodes can communicate again")
        self._reconcile()
    
    def write_to_node_a(self, value: int) -> Optional[bool]:
        """Attempt to write to Node A."""
        if self.partitioned:
            if self.system_type == SystemType.CP:
                print(f"❌ CP System: Write to {self.node_a.name} REJECTED (cannot guarantee consistency)")
                return None  # Unavailable during partition
            else:  # AP system
                print(f"⚠️  AP System: Write to {self.node_a.name} ACCEPTED (may diverge from {self.node_b.name})")
                self.node_a.write(value)
                return True
        else:
            # No partition - both nodes sync
            self.node_a.write(value)
            self.node_b.write(value)
            print(f"✓ Write {value} synchronized to both nodes")
            return True
    
    def read_from_node_b(self) -> Optional[int]:
        """Read from Node B."""
        if self.partitioned and self.system_type == SystemType.CP:
            print(f"❌ CP System: Read from {self.node_b.name} REJECTED (cannot guarantee consistency)")
            return None
        print(f"📖 Read from {self.node_b.name}: {self.node_b.value}")
        return self.node_b.value
    
    def _reconcile(self) -> None:
        """Reconcile divergent values after partition heals."""
        if self.node_a.value != self.node_b.value:
            # Last-write-wins strategy
            if self.node_a.last_sync > self.node_b.last_sync:
                winner = self.node_a
                self.node_b.value = self.node_a.value
            else:
                winner = self.node_b
                self.node_a.value = self.node_b.value
            print(f"🔄 Reconciled: {winner.name}'s value ({winner.value}) wins (last-write-wins)")
    
    def status(self) -> str:
        """Return current system status."""
        partition_status = "🔴 PARTITIONED" if self.partitioned else "🟢 CONNECTED"
        return (
            f"\n{'='*50}\n"
            f"System: {self.system_type.value}\n"
            f"Status: {partition_status}\n"
            f"  {self.node_a.name}: value={self.node_a.value}\n"
            f"  {self.node_b.name}: value={self.node_b.value}\n"
            f"{'='*50}"
        )


def demonstrate_cap_theorem():
    """Run the CAP theorem demonstration."""
    print("\n" + "="*60)
    print("   CAP THEOREM DEMONSTRATION")
    print("   Simulating Network Partitions in Distributed Systems")
    print("="*60)
    
    # Demonstrate CP System
    print("\n\n📊 SCENARIO 1: CP System (like ZooKeeper)")
    print("-" * 40)
    cp_system = DistributedSystem(SystemType.CP)
    
    print("\n1. Normal operation - write value 42:")
    cp_system.write_to_node_a(42)
    print(cp_system.status())
    
    print("\n2. Network partition occurs:")
    cp_system.create_partition()
    
    print("\n3. Attempting write during partition:")
    cp_system.write_to_node_a(100)
    
    print("\n4. Attempting read during partition:")
    cp_system.read_from_node_b()
    print(cp_system.status())
    
    print("\n5. Partition heals:")
    cp_system.heal_partition()
    print(cp_system.status())
    
    # Demonstrate AP System
    print("\n\n📊 SCENARIO 2: AP System (like Cassandra)")
    print("-" * 40)
    ap_system = DistributedSystem(SystemType.AP)
    
    print("\n1. Normal operation - write value 42:")
    ap_system.write_to_node_a(42)
    print(ap_system.status())
    
    print("\n2. Network partition occurs:")
    ap_system.create_partition()
    
    print("\n3. Attempting write during partition:")
    ap_system.write_to_node_a(100)
    
    print("\n4. Attempting read from other node during partition:")
    ap_system.read_from_node_b()
    print(ap_system.status())
    print("   ⚠️  Note: Nodes have DIVERGENT values!")
    
    print("\n5. Partition heals:")
    ap_system.heal_partition()
    print(ap_system.status())
    
    print("\n\n" + "="*60)
    print("   CONCLUSION")
    print("="*60)
    print("""
    CP Systems: Sacrifice AVAILABILITY during partitions
    - Writes/reads may be rejected
    - But data stays consistent
    
    AP Systems: Sacrifice CONSISTENCY during partitions  
    - Writes/reads always succeed
    - But nodes may temporarily diverge
    
    The CAP theorem proves you CANNOT have both during a partition.
    """)
    
    return True


if __name__ == "__main__":
    success = demonstrate_cap_theorem()
    print(f"\n✅ Demonstration completed successfully: {success}")
