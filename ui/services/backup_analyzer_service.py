"""Fachada de compatibilidad hacia atrás para backup_analyzer_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.backup_analyzer_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.backup_analyzer_service")
sys.modules[__name__] = _target
