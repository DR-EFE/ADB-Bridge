"""Fachada de compatibilidad hacia atrás para device_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.device_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.device_service")
sys.modules[__name__] = _target
