"""Fachada de compatibilidad hacia atrás para adb_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.adb.adb_service
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.adb.adb_service")
sys.modules[__name__] = _target
