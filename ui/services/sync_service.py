"""Fachada de compatibilidad hacia atrás para sync_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.sync_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.sync_service")
sys.modules[__name__] = _target
