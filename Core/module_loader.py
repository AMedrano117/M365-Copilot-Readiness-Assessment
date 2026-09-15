"""
Module loading progress bar for recommendation modules
"""
import sys
from .spinner import _stdout_lock
from datetime import datetime
from . import console_reporting as console

def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class ModuleLoadingProgress:
    def __init__(self):
        self.total_services = 0
        self.loaded_services = 0
        self.total_modules = 0
        self.service_names = []
        self.started = False
        
    def start(self, total_services):
        """Start progress bar with expected number of services"""
        self.total_services = total_services
        self.started = True
        with _stdout_lock:
            console.detail('Loading recommendation modules.')
        
    def update(self, service_name, module_count):
        """Update progress when a service finishes loading modules"""
        with _stdout_lock:
            self.loaded_services += 1
            self.total_modules += module_count
            self.service_names.append(service_name)

            # A recommendation package can be imported without the orchestrator having called
            # start() first - by tests, tooling, or a direct import. Drawing a progress bar is
            # not worth crashing the import over, so skip rendering when no total was set.
            if not self.started or self.total_services <= 0:
                return

            console.detail(f'{service_name}: loaded {module_count} recommendation modules.')

# Global instance
_module_progress = None

def get_progress_tracker():
    """Get or create the global progress tracker"""
    global _module_progress
    if _module_progress is None:
        _module_progress = ModuleLoadingProgress()
    return _module_progress

def start_module_loading(total_services):
    """Initialize and show the progress bar with the expected number of services"""
    global _module_progress
    _module_progress = ModuleLoadingProgress()
    _module_progress.start(total_services)
