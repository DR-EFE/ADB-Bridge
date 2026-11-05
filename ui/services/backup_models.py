"""Fachada de compatibilidad hacia atrás para backup_models.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.models.backup
"""
import sys
import importlib

_target = importlib.import_module("core.domain.models.backup")
sys.modules[__name__] = _target
