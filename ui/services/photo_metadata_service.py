"""Fachada de compatibilidad hacia atrás para photo_metadata_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.photo_metadata_service
"""
import sys
import importlib

_target = importlib.import_module("core.services.photo_metadata_service")
sys.modules[__name__] = _target

