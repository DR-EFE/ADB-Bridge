"""Fachada de compatibilidad hacia atrás para transfer_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.transfer_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.transfer_service")
sys.modules[__name__] = _target
