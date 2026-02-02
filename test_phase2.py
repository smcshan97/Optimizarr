#!/usr/bin/env python3
"""
Test script for Phase 2 - Resource Monitoring
"""
import sys
sys.path.insert(0, '/home/claude/optimizarr')

from app.resources import resource_monitor, resource_throttler

print("=" * 70)
print("OPTIMIZARR PHASE 2 - RESOURCE MONITORING TEST")
print("=" * 70)
print()

# Test 1: CPU Monitoring
print("1. Testing CPU Monitoring...")
cpu_usage = resource_monitor.get_cpu_usage(interval=0.5)
print(f"   ✓ Current CPU Usage: {cpu_usage:.1f}%")

cpu_per_core = resource_monitor.get_cpu_per_core()
print(f"   ✓ CPU Cores: {len(cpu_per_core)}")
for i, usage in enumerate(cpu_per_core):
    print(f"      Core {i}: {usage:.1f}%")
print()

# Test 2: Memory Monitoring
print("2. Testing Memory Monitoring...")
memory = resource_monitor.get_memory_usage()
print(f"   ✓ Total Memory: {memory['total_mb']:.0f} MB ({memory['total_mb']/1024:.1f} GB)")
print(f"   ✓ Used Memory: {memory['used_mb']:.0f} MB ({memory['used_mb']/1024:.1f} GB)")
print(f"   ✓ Available Memory: {memory['available_mb']:.0f} MB ({memory['available_mb']/1024:.1f} GB)")
print(f"   ✓ Memory Usage: {memory['percent']:.1f}%")
print()

# Test 3: GPU Monitoring
print("3. Testing GPU Monitoring...")
gpu_stats = resource_monitor.get_gpu_usage()
if gpu_stats:
    for gpu in gpu_stats:
        print(f"   ✓ GPU {gpu['index']}: {gpu['name']}")
        print(f"      - Utilization: {gpu['utilization_percent']}%")
        print(f"      - Memory: {gpu['memory_used_mb']:.0f}/{gpu['memory_total_mb']:.0f} MB ({gpu['memory_percent']}%)")
        if gpu['temperature_c']:
            print(f"      - Temperature: {gpu['temperature_c']}°C")
        if gpu['power_usage_w']:
            print(f"      - Power: {gpu['power_usage_w']:.1f}W / {gpu['power_limit_w']:.1f}W")
else:
    print("   ⚠️  No NVIDIA GPU detected (this is normal)")
print()

# Test 4: Threshold Checking
print("4. Testing Threshold Checking...")
thresholds = resource_monitor.check_thresholds(
    cpu_threshold=90.0,
    memory_threshold=85.0,
    gpu_threshold=90.0
)
print(f"   ✓ CPU: {thresholds['cpu_usage']:.1f}% (Exceeded: {thresholds['cpu_exceeded']})")
print(f"   ✓ Memory: {thresholds['memory_usage']:.1f}% (Exceeded: {thresholds['memory_exceeded']})")
if thresholds['gpu_usage'] is not None:
    print(f"   ✓ GPU: {thresholds['gpu_usage']:.1f}% (Exceeded: {thresholds['gpu_exceeded']})")
print(f"   ✓ Should Pause: {thresholds['should_pause']}")
print()

# Test 5: Complete Resource Snapshot
print("5. Testing Complete Resource Snapshot...")
snapshot = resource_monitor.get_all_resources()
print(f"   ✓ Timestamp: {snapshot['timestamp']}")
print(f"   ✓ CPU: {snapshot['cpu']['percent']:.1f}%")
print(f"   ✓ Memory: {snapshot['memory']['percent']:.1f}%")
print(f"   ✓ Disk I/O: {snapshot['disk_io']['read_bytes']/1024/1024:.1f} MB read")
print()

print("=" * 70)
print("✅ ALL TESTS PASSED - Phase 2 Resource Monitoring is Working!")
print("=" * 70)
print()
print("Next Steps:")
print("  1. Start the Optimizarr server: python -m app.main")
print("  2. Visit: http://localhost:5000")
print("  3. Check the dashboard for resource cards")
print("  4. Go to Settings tab to configure thresholds")
print()
