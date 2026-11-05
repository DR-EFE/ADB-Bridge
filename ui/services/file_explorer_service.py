"""Fachada de compatibilidad hacia atrás para file_explorer_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.file_explorer_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.file_explorer_service")
sys.modules[__name__] = _target
