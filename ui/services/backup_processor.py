"""Fachada de compatibilidad hacia atrás para backup_processor.py.
Redirige a la capa canónica en la arquitectura por capas: core.services.backup_processor
"""
import sys
import importlib

_target = importlib.import_module("core.services.backup_processor")
sys.modules[__name__] = _target
