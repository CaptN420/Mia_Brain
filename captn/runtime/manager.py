import importlib.util
import os
import sys
from typing import Dict, List, Optional
from captn.runtime.base import Plugin

class PluginManager:
    def __init__(self, plugins_dir: str, bus):
        self.plugins_dir = plugins_dir
        self.bus = bus
        self.loaded_plugins: Dict[str, Plugin] = {}

    def register_plugin(self, plugin: Plugin):
        """Register a plugin instance."""
        if hasattr(plugin, 'name'):
            self.loaded_plugins[plugin.name] = plugin
            print(f"Registered plugin: {plugin.name}")
        else:
            raise AttributeError(f"Plugin must have a 'name' attribute. Got: {type(plugin).__name__}")

    def discover_plugins(self) -> List[str]:
        """Find all .py files in the plugins directory."""
        plugin_files = []
        if not os.path.exists(self.plugins_dir):
            return []
        for file in os.listdir(self.plugins_dir):
            if file.endswith(".py") and file != "__init__.py":
                plugin_files.append(file)
        return plugin_files

    def load_plugin(self, plugin_file: str) -> Optional[Plugin]:
        """Dynamically import and instantiate a plugin."""
        file_path = os.path.join(self.plugins_dir, plugin_file)
        module_name = plugin_file[:-3]
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # Look for a class that inherits from Plugin
            for name, obj in vars(module).items():
                if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin:
                    # Pass the bus to the plugin
                    instance = obj(self.bus)
                    if instance.initialize():
                        self.loaded_plugins[instance.name] = instance
                        print(f"Successfully loaded plugin: {instance.name}")
                        return instance
        except Exception as e:
            print(f"Failed to load plugin {plugin_file}: {e}")
        return None

    def get_plugin(self, name: str) -> Optional[Plugin]:
        return self.loaded_plugins.get(name)

    def unload_plugin(self, name: str):
        if name in self.loaded_plugins:
            self.loaded_plugins[name].shutdown()
            del self.loaded_plugins[name]
            print(f"Unloaded plugin: {name}")
