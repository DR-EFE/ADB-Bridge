"""Fachada de compatibilidad hacia atrás para automation_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.automation_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.automation_service")
sys.modules[__name__] = _target
