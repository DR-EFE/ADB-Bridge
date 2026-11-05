"""Fachada de compatibilidad hacia atrás para backup_profiles.py.
Redirige a la capa canónica en la arquitectura por capas: core.domain.rules.backup_profiles
"""
import sys
import importlib

_target = importlib.import_module("core.domain.rules.backup_profiles")
sys.modules[__name__] = _target
