"""
Resource monitoring module for Optimizarr.
Monitors CPU, memory, GPU, and disk I/O to enable intelligent throttling.
"""
import psutil
import time
from typing import Dict, Optional, List
from datetime import datetime


class ResourceMonitor:
    """Monitors system resources (CPU, memory, GPU, disk I/O)."""
    
    def __init__(self):
        self.gpu_available = self._init_gpu_monitoring()
        self.monitoring_interval = 2.0  # seconds
        self._last_sample = None
        
    def _init_gpu_monitoring(self) -> bool:
        """Initialize GPU monitoring if NVIDIA GPU is available."""
        try:
            import pynvml
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            print(f"✓ GPU monitoring enabled: {device_count} NVIDIA GPU(s) detected")
            return True
        except Exception as e:
            print(f"⚠ GPU monitoring not available: {e}")
            return False
    
    def get_cpu_usage(self, interval: float = 1.0) -> float:
        """
        Get current CPU usage percentage.
        
        Args:
            interval: Sampling interval in seconds
            
        Returns:
            CPU usage percentage (0-100)
        """
        return psutil.cpu_percent(interval=interval)
    
    def get_cpu_per_core(self) -> List[float]:
        """Get CPU usage per core."""
        return psutil.cpu_percent(interval=1.0, percpu=True)
    
    def get_memory_usage(self) -> Dict[str, float]:
        """
        Get memory usage statistics.
        
        Returns:
            Dict with total, available, percent, used (in MB)
        """
        mem = psutil.virtual_memory()
        return {
            'total_mb': mem.total / (1024 ** 2),
            'available_mb': mem.available / (1024 ** 2),
            'used_mb': mem.used / (1024 ** 2),
            'percent': mem.percent
        }
    
    def get_disk_io(self) -> Dict[str, int]:
        """
        Get disk I/O statistics.
        
        Returns:
            Dict with read_bytes, write_bytes, read_count, write_count
        """
        io = psutil.disk_io_counters()
        if io:
            return {
                'read_bytes': io.read_bytes,
                'write_bytes': io.write_bytes,
                'read_count': io.read_count,
                'write_count': io.write_count
            }
        return {
            'read_bytes': 0,
            'write_bytes': 0,
            'read_count': 0,
            'write_count': 0
        }
    
    def get_gpu_usage(self) -> Optional[List[Dict]]:
        """
        Get GPU usage for all NVIDIA GPUs.
        
        Returns:
            List of dicts with GPU stats, or None if GPU monitoring unavailable
        """
        if not self.gpu_available:
            return None
        
        try:
            import pynvml
            device_count = pynvml.nvmlDeviceGetCount()
            gpu_stats = []
            
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                
                # GPU name
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                
                # GPU utilization
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                
                # Memory info
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                
                # Temperature
                try:
                    temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except:
                    temperature = None
                
                # Power usage
                try:
                    power_usage = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # Convert to watts
                    power_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
                except:
                    power_usage = None
                    power_limit = None
                
                gpu_stats.append({
                    'index': i,
                    'name': name,
                    'utilization_percent': utilization.gpu,
                    'memory_percent': utilization.memory,
                    'memory_used_mb': mem_info.used / (1024 ** 2),
                    'memory_total_mb': mem_info.total / (1024 ** 2),
                    'temperature_c': temperature,
                    'power_usage_w': power_usage,
                    'power_limit_w': power_limit
                })
            
            return gpu_stats
            
        except Exception as e:
            print(f"Error getting GPU stats: {e}")
            return None
    
    def get_process_resources(self, pid: int) -> Optional[Dict]:
        """
        Get resource usage for a specific process.
        
        Args:
            pid: Process ID
            
        Returns:
            Dict with CPU and memory usage, or None if process not found
        """
        try:
            process = psutil.Process(pid)
            
            return {
                'pid': pid,
                'cpu_percent': process.cpu_percent(interval=1.0),
                'memory_mb': process.memory_info().rss / (1024 ** 2),
                'memory_percent': process.memory_percent(),
                'num_threads': process.num_threads(),
                'status': process.status()
            }
        except psutil.NoSuchProcess:
            return None
    
    def get_all_resources(self) -> Dict:
        """
        Get complete resource snapshot.
        
        Returns:
            Dict with CPU, memory, disk I/O, and GPU stats
        """
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'cpu': {
                'percent': self.get_cpu_usage(interval=0.5),
                'per_core': self.get_cpu_per_core(),
                'count': psutil.cpu_count()
            },
            'memory': self.get_memory_usage(),
            'disk_io': self.get_disk_io(),
            'gpu': self.get_gpu_usage()
        }
    
    def check_thresholds(self, cpu_threshold: float = 90.0, 
                        memory_threshold: float = 85.0,
                        gpu_threshold: float = 90.0) -> Dict[str, bool]:
        """
        Check if resource usage exceeds thresholds.
        
        Args:
            cpu_threshold: CPU usage threshold percentage
            memory_threshold: Memory usage threshold percentage
            gpu_threshold: GPU usage threshold percentage
            
        Returns:
            Dict with boolean flags for each resource type
        """
        cpu_usage = self.get_cpu_usage(interval=0.5)
        memory_usage = self.get_memory_usage()['percent']
        
        result = {
            'cpu_exceeded': cpu_usage > cpu_threshold,
            'memory_exceeded': memory_usage > memory_threshold,
            'gpu_exceeded': False,
            'should_pause': False,
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'gpu_usage': None
        }
        
        # Check GPU if available
        if self.gpu_available:
            gpu_stats = self.get_gpu_usage()
            if gpu_stats:
                max_gpu_usage = max(gpu['utilization_percent'] for gpu in gpu_stats)
                result['gpu_usage'] = max_gpu_usage
                result['gpu_exceeded'] = max_gpu_usage > gpu_threshold
        
        # Determine if encoding should pause
        result['should_pause'] = (
            result['cpu_exceeded'] or 
            result['memory_exceeded'] or 
            result['gpu_exceeded']
        )
        
        return result


class ResourceThrottler:
    """Manages process throttling based on resource thresholds."""
    
    def __init__(self, monitor: ResourceMonitor):
        self.monitor = monitor
        self.check_interval = 5.0  # Check every 5 seconds
        self.last_check = 0
        
    def should_pause_encoding(self, cpu_threshold: float = 90.0,
                             memory_threshold: float = 85.0,
                             gpu_threshold: float = 90.0) -> tuple[bool, str]:
        """
        Check if encoding should be paused due to resource constraints.
        
        Returns:
            Tuple of (should_pause, reason)
        """
        current_time = time.time()
        
        # Only check at specified intervals
        if current_time - self.last_check < self.check_interval:
            return False, ""
        
        self.last_check = current_time
        
        # Check thresholds
        result = self.monitor.check_thresholds(
            cpu_threshold=cpu_threshold,
            memory_threshold=memory_threshold,
            gpu_threshold=gpu_threshold
        )
        
        if not result['should_pause']:
            return False, ""
        
        # Build reason message
        reasons = []
        if result['cpu_exceeded']:
            reasons.append(f"CPU usage {result['cpu_usage']:.1f}% exceeds threshold {cpu_threshold}%")
        if result['memory_exceeded']:
            reasons.append(f"Memory usage {result['memory_usage']:.1f}% exceeds threshold {memory_threshold}%")
        if result['gpu_exceeded'] and result['gpu_usage']:
            reasons.append(f"GPU usage {result['gpu_usage']:.1f}% exceeds threshold {gpu_threshold}%")
        
        return True, "; ".join(reasons)
    
    def set_process_priority(self, pid: int, nice_level: int = 10):
        """
        Set process priority (nice level).
        
        Args:
            pid: Process ID
            nice_level: Nice level (-20 to 19, higher = lower priority)
        """
        try:
            process = psutil.Process(pid)
            
            # Set nice level (Unix) or priority class (Windows)
            if hasattr(process, 'nice'):
                process.nice(nice_level)
                print(f"Set process {pid} nice level to {nice_level}")
            else:
                # Windows priority classes
                import psutil
                if nice_level >= 15:
                    process.nice(psutil.IDLE_PRIORITY_CLASS)
                elif nice_level >= 10:
                    process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                else:
                    process.nice(psutil.NORMAL_PRIORITY_CLASS)
                print(f"Set process {pid} priority class")
                
        except Exception as e:
            print(f"Error setting process priority: {e}")
    
    def set_cpu_affinity(self, pid: int, cpu_list: List[int]):
        """
        Set CPU affinity for a process.
        
        Args:
            pid: Process ID
            cpu_list: List of CPU core numbers to use
        """
        try:
            process = psutil.Process(pid)
            process.cpu_affinity(cpu_list)
            print(f"Set process {pid} CPU affinity to cores: {cpu_list}")
        except Exception as e:
            print(f"Error setting CPU affinity: {e}")


# Global instances
resource_monitor = ResourceMonitor()
resource_throttler = ResourceThrottler(resource_monitor)
