"""Fachada de compatibilidad hacia atrás para device_health_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.device_health_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.device_health_service")
sys.modules[__name__] = _target
