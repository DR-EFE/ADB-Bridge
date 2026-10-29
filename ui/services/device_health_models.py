"""Fachada de compatibilidad hacia atrás para device_health_models.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.models.device_health
"""
import sys
import importlib

_target = importlib.import_module("core.domain.models.device_health")
sys.modules[__name__] = _target
