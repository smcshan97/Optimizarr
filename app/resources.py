"""
Resource monitoring module for Optimizarr.
Monitors CPU, memory, GPU, and disk I/O to enable intelligent throttling.

Throttle logic centres on **temperature** — not utilisation — because
HandBrakeCLI will peg a CPU or GPU to 100 % by design.  Pausing on
utilisation would deadlock every encode.  Temperature tells you when the
hardware is actually in danger; utilisation stays available as an
*optional* secondary trigger for users who want it.
"""
import warnings
# Suppress the FutureWarning from the deprecated pynvml package at import time.
# nvidia-ml-py is the maintained replacement; install it to remove pynvml entirely.
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")
warnings.filterwarnings("ignore", message=".*pynvml.*deprecated.*", category=FutureWarning)

import os
import platform
import psutil
import subprocess
import json
import time
from typing import Dict, Optional, List, Tuple
from datetime import datetime


class ResourceMonitor:
    """Monitors system resources (CPU, memory, GPU, disk I/O)."""

    def __init__(self):
        self._gpu_method = None  # 'pynvml' or 'nvidia-smi' or None
        self.gpu_available = self._init_gpu_monitoring()
        self.monitoring_interval = 2.0  # seconds
        self._last_sample = None
        self._cpu_temp_method = self._detect_cpu_temp_method()

    # ------------------------------------------------------------------
    # GPU initialisation (unchanged from prior implementation)
    # ------------------------------------------------------------------
    def _init_gpu_monitoring(self) -> bool:
        """Initialize GPU monitoring if NVIDIA GPU is available."""
        nvml = None
        for pkg in ('nvidia_ml_py', 'pynvml'):
            try:
                import importlib
                nvml = importlib.import_module(pkg if pkg == 'pynvml' else 'pynvml')
                break
            except ImportError:
                continue

        if nvml:
            try:
                import warnings as _w
                with _w.catch_warnings():
                    _w.simplefilter("ignore", FutureWarning)
                    import pynvml
                    pynvml.nvmlInit()
                device_count = pynvml.nvmlDeviceGetCount()
                if device_count > 0:
                    name = pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    print(f"GPU monitoring enabled: {device_count} NVIDIA GPU(s) detected - {name}")
                    self._gpu_method = 'pynvml'
                    return True
            except Exception as e:
                print(f"nvml init failed ({e}), trying nvidia-smi fallback...")

        # Fallback: try nvidia-smi
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_names = result.stdout.strip().split('\n')
                print(f"GPU monitoring enabled (nvidia-smi): {len(gpu_names)} NVIDIA GPU(s) detected - {gpu_names[0].strip()}")
                self._gpu_method = 'nvidia-smi'
                return True
        except FileNotFoundError:
            print("nvidia-smi not found in PATH")
        except Exception as e:
            print(f"nvidia-smi fallback failed: {e}")

        print("GPU monitoring not available: no NVIDIA GPU detected or drivers not installed")
        return False

    # ------------------------------------------------------------------
    # CPU temperature
    # ------------------------------------------------------------------
    def _detect_cpu_temp_method(self) -> Optional[str]:
        """Detect which method is available for reading CPU temperature."""
        # Linux: psutil.sensors_temperatures()
        if hasattr(psutil, 'sensors_temperatures'):
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    print(f"CPU temperature monitoring enabled (psutil sensors: {list(temps.keys())})")
                    return 'psutil'
            except Exception:
                pass

        # Windows: WMI via PowerShell
        if platform.system() == 'Windows':
            try:
                creation_flags = 0
                if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    creation_flags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     'Get-CimInstance MSAcpi_ThermalZoneTemperature '
                     '-Namespace root/WMI -ErrorAction Stop '
                     '| Select-Object -First 1 -ExpandProperty CurrentTemperature'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=creation_flags
                )
                if result.returncode == 0 and result.stdout.strip():
                    raw = float(result.stdout.strip().split('\n')[0])
                    celsius = (raw / 10.0) - 273.15
                    if 0 < celsius < 150:
                        print(f"CPU temperature monitoring enabled (WMI) - initial reading: {celsius:.1f} C")
                        return 'wmi'
            except Exception as e:
                print(f"WMI temperature probe failed: {e}")

        print("CPU temperature monitoring not available - "
              "GPU temperature and memory % can still be used as pause triggers")
        return None

    def get_cpu_temperature(self) -> Optional[float]:
        """
        Read the current CPU temperature in degrees C.

        Returns the hottest reading across all sensor groups.
        Returns None when no method is available (the UI will display
        "N/A" and the CPU-temp pause trigger will be automatically
        disabled).
        """
        if self._cpu_temp_method == 'psutil':
            return self._cpu_temp_psutil()
        elif self._cpu_temp_method == 'wmi':
            return self._cpu_temp_wmi()
        return None

    def _cpu_temp_psutil(self) -> Optional[float]:
        """Read CPU temp via psutil (Linux / macOS)."""
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None
            # Prefer well-known CPU sensor names
            for name in ('coretemp', 'k10temp', 'zenpower', 'cpu_thermal',
                         'acpitz', 'soc_thermal', 'Tctl'):
                if name in temps and temps[name]:
                    return max(t.current for t in temps[name])
            # Fallback: hottest reading from any sensor
            all_temps = [t.current for sensors in temps.values()
                         for t in sensors if t.current > 0]
            return max(all_temps) if all_temps else None
        except Exception:
            return None

    def _cpu_temp_wmi(self) -> Optional[float]:
        """Read CPU temp via WMI (Windows).

        Get-CimInstance MSAcpi_ThermalZoneTemperature returns tenths of
        Kelvin.  Convert:  (raw / 10) - 273.15 = degrees C.
        Requires running as administrator on most systems.
        """
        try:
            creation_flags = 0
            if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                creation_flags = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-CimInstance MSAcpi_ThermalZoneTemperature '
                 '-Namespace root/WMI -ErrorAction Stop '
                 '| Select-Object -ExpandProperty CurrentTemperature'],
                capture_output=True, text=True, timeout=5,
                creationflags=creation_flags
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            readings = []
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = float(line)
                    celsius = (raw / 10.0) - 273.15
                    if 0 < celsius < 150:
                        readings.append(celsius)
                except ValueError:
                    continue
            return round(max(readings), 1) if readings else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # CPU utilisation
    # ------------------------------------------------------------------
    def get_cpu_usage(self, interval: float = 1.0) -> float:
        """Get current CPU usage percentage (0-100)."""
        return psutil.cpu_percent(interval=interval)

    def get_cpu_per_core(self) -> List[float]:
        """Get CPU usage per core."""
        return psutil.cpu_percent(interval=1.0, percpu=True)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    def get_memory_usage(self) -> Dict[str, float]:
        """Get memory usage statistics (MB and percent)."""
        mem = psutil.virtual_memory()
        return {
            'total_mb': mem.total / (1024 ** 2),
            'available_mb': mem.available / (1024 ** 2),
            'used_mb': mem.used / (1024 ** 2),
            'percent': mem.percent
        }

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------
    def get_disk_io(self) -> Dict[str, int]:
        """Get disk I/O statistics."""
        io = psutil.disk_io_counters()
        if io:
            return {
                'read_bytes': io.read_bytes,
                'write_bytes': io.write_bytes,
                'read_count': io.read_count,
                'write_count': io.write_count
            }
        return {'read_bytes': 0, 'write_bytes': 0, 'read_count': 0, 'write_count': 0}

    # ------------------------------------------------------------------
    # GPU (NVIDIA)
    # ------------------------------------------------------------------
    def get_gpu_usage(self) -> Optional[List[Dict]]:
        """Get GPU usage for all NVIDIA GPUs."""
        if not self.gpu_available:
            return None
        if self._gpu_method == 'pynvml':
            return self._get_gpu_usage_pynvml()
        elif self._gpu_method == 'nvidia-smi':
            return self._get_gpu_usage_smi()
        return None

    def _get_gpu_usage_pynvml(self) -> Optional[List[Dict]]:
        """Get GPU stats via pynvml."""
        try:
            import pynvml
            device_count = pynvml.nvmlDeviceGetCount()
            gpu_stats = []

            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)

                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')

                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)

                try:
                    temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception:
                    temperature = None

                try:
                    power_usage = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                    power_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
                except Exception:
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
            print(f"Error getting GPU stats via pynvml: {e}")
            self._gpu_method = 'nvidia-smi'
            return self._get_gpu_usage_smi()

    def _get_gpu_usage_smi(self) -> Optional[List[Dict]]:
        """Get GPU stats via nvidia-smi command (fallback)."""
        try:
            result = subprocess.run(
                [
                    'nvidia-smi',
                    '--query-gpu=index,name,utilization.gpu,utilization.memory,'
                    'memory.used,memory.total,temperature.gpu,power.draw,power.limit',
                    '--format=csv,noheader,nounits'
                ],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None

            def safe_float(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            gpu_stats = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 9:
                    continue
                gpu_stats.append({
                    'index': int(parts[0]),
                    'name': parts[1],
                    'utilization_percent': safe_float(parts[2]) or 0,
                    'memory_percent': safe_float(parts[3]) or 0,
                    'memory_used_mb': safe_float(parts[4]) or 0,
                    'memory_total_mb': safe_float(parts[5]) or 0,
                    'temperature_c': safe_float(parts[6]),
                    'power_usage_w': safe_float(parts[7]),
                    'power_limit_w': safe_float(parts[8])
                })

            return gpu_stats if gpu_stats else None

        except Exception as e:
            print(f"Error getting GPU stats via nvidia-smi: {e}")
            return None

    # ------------------------------------------------------------------
    # Per-process resources
    # ------------------------------------------------------------------
    def get_process_resources(self, pid: int) -> Optional[Dict]:
        """Get resource usage for a specific process."""
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

    # ------------------------------------------------------------------
    # Full snapshot (consumed by /api/resources/current)
    # ------------------------------------------------------------------
    def get_all_resources(self) -> Dict:
        """Get complete resource snapshot including temperatures."""
        cpu_temp = self.get_cpu_temperature()
        gpu_data = self.get_gpu_usage()

        return {
            'timestamp': datetime.utcnow().isoformat(),
            'cpu': {
                'percent': self.get_cpu_usage(interval=0.5),
                'per_core': self.get_cpu_per_core(),
                'count': psutil.cpu_count(),
                'temperature_c': cpu_temp,
                'temp_available': cpu_temp is not None,
            },
            'memory': self.get_memory_usage(),
            'disk_io': self.get_disk_io(),
            'gpu': gpu_data
        }

    # ------------------------------------------------------------------
    # Threshold checking - temperature-first, toggleable triggers
    # ------------------------------------------------------------------
    def check_thresholds(self, *,
                         pause_on_cpu_temp: bool = True,
                         cpu_temp_threshold: float = 85.0,
                         pause_on_gpu_temp: bool = True,
                         gpu_temp_threshold: float = 83.0,
                         pause_on_memory: bool = False,
                         memory_threshold: float = 85.0,
                         pause_on_cpu_usage: bool = False,
                         cpu_usage_threshold: float = 95.0) -> Dict:
        """
        Evaluate all enabled pause triggers and return a result dict.

        Each trigger is independently toggleable.  The should_pause
        flag is True when any enabled trigger fires.

        Parameters use keyword-only syntax so callers must be explicit.
        """
        result = {
            'should_pause': False,
            # Raw readings (always populated for the dashboard)
            'cpu_temp_c': None,
            'gpu_temp_c': None,
            'cpu_usage': None,
            'memory_usage': None,
            # Per-trigger exceeded flags
            'cpu_temp_exceeded': False,
            'gpu_temp_exceeded': False,
            'memory_exceeded': False,
            'cpu_usage_exceeded': False,
        }

        reasons: List[str] = []

        # --- CPU temperature ---
        cpu_temp = self.get_cpu_temperature()
        result['cpu_temp_c'] = cpu_temp
        if pause_on_cpu_temp and cpu_temp is not None and cpu_temp > cpu_temp_threshold:
            result['cpu_temp_exceeded'] = True
            reasons.append(f"CPU temp {cpu_temp:.0f}C > {cpu_temp_threshold:.0f}C")

        # --- GPU temperature ---
        gpu_stats = self.get_gpu_usage() if self.gpu_available else None
        if gpu_stats:
            temps = [g['temperature_c'] for g in gpu_stats if g.get('temperature_c') is not None]
            if temps:
                hottest = max(temps)
                result['gpu_temp_c'] = hottest
                if pause_on_gpu_temp and hottest > gpu_temp_threshold:
                    result['gpu_temp_exceeded'] = True
                    reasons.append(f"GPU temp {hottest:.0f}C > {gpu_temp_threshold:.0f}C")

        # --- Memory % ---
        mem_pct = self.get_memory_usage()['percent']
        result['memory_usage'] = mem_pct
        if pause_on_memory and mem_pct > memory_threshold:
            result['memory_exceeded'] = True
            reasons.append(f"Memory {mem_pct:.1f}% > {memory_threshold:.0f}%")

        # --- CPU usage % (optional legacy trigger) ---
        cpu_pct = self.get_cpu_usage(interval=0.5)
        result['cpu_usage'] = cpu_pct
        if pause_on_cpu_usage and cpu_pct > cpu_usage_threshold:
            result['cpu_usage_exceeded'] = True
            reasons.append(f"CPU usage {cpu_pct:.1f}% > {cpu_usage_threshold:.0f}%")

        result['should_pause'] = bool(reasons)
        result['reasons'] = reasons
        return result


class ResourceThrottler:
    """Manages process throttling based on resource thresholds."""

    def __init__(self, monitor: ResourceMonitor):
        self.monitor = monitor
        self.check_interval = 5.0  # seconds between checks
        self.last_check = 0

    def should_pause_encoding(self, **kwargs) -> Tuple[bool, str]:
        """
        Check if encoding should be paused due to resource constraints.

        Accepts the same keyword arguments as
        ResourceMonitor.check_thresholds().

        Returns:
            Tuple of (should_pause: bool, reason_string: str)
        """
        current_time = time.time()

        # Rate-limit checks
        if current_time - self.last_check < self.check_interval:
            return False, ""

        self.last_check = current_time

        result = self.monitor.check_thresholds(**kwargs)

        if not result['should_pause']:
            return False, ""

        return True, "; ".join(result.get('reasons', []))

    def set_process_priority(self, pid: int, nice_level: int = 10):
        """Set process priority (nice level / Windows priority class)."""
        try:
            process = psutil.Process(pid)

            if platform.system() == 'Windows':
                if nice_level >= 15:
                    process.nice(psutil.IDLE_PRIORITY_CLASS)
                elif nice_level >= 10:
                    process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                else:
                    process.nice(psutil.NORMAL_PRIORITY_CLASS)
                print(f"Set process {pid} to Windows priority class (nice={nice_level})")
            else:
                process.nice(nice_level)
                print(f"Set process {pid} nice level to {nice_level}")
        except Exception as e:
            print(f"Error setting process priority: {e}")

    def set_cpu_affinity(self, pid: int, cpu_list: List[int]):
        """Set CPU affinity for a process."""
        try:
            process = psutil.Process(pid)
            process.cpu_affinity(cpu_list)
            print(f"Set process {pid} CPU affinity to cores: {cpu_list}")
        except Exception as e:
            print(f"Error setting CPU affinity: {e}")


# Global instances
resource_monitor = ResourceMonitor()
resource_throttler = ResourceThrottler(resource_monitor)
