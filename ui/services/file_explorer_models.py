"""Fachada de compatibilidad hacia atrás para file_explorer_models.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.models.explorer
"""
import sys
import importlib

_target = importlib.import_module("core.domain.models.explorer")
sys.modules[__name__] = _target
