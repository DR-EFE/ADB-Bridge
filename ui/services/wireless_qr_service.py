"""Fachada de compatibilidad hacia atrás para wireless_qr_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.adb.wireless_qr
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.adb.wireless_qr")
sys.modules[__name__] = _target
