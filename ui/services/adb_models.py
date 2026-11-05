"""Fachada de compatibilidad hacia atrás para adb_models.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.models.adb
"""
import sys
import importlib

_target = importlib.import_module("core.domain.models.adb")
sys.modules[__name__] = _target
