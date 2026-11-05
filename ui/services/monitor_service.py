"""Fachada de compatibilidad hacia atrás para monitor_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.system.monitor_service
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.system.monitor_service")
sys.modules[__name__] = _target
