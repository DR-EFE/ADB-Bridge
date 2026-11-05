"""Fachada de compatibilidad hacia atrás para database.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.database.sqlite
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.database.sqlite")
sys.modules[__name__] = _target
