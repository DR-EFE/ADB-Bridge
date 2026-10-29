"""Fachada de compatibilidad hacia atrás para backup_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.backup_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.backup_service")
sys.modules[__name__] = _target
