"""Fachada de compatibilidad hacia atrás para resumable_transfer.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.adb.resumable_transfer
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.adb.resumable_transfer")
sys.modules[__name__] = _target
