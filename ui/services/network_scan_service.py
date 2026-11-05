"""Fachada de compatibilidad hacia atrás para network_scan_service.py.
Redirige a la capa canónica en la arquitectura por capas: core.infrastructure.network.network_scan
"""
import sys
import importlib

_target = importlib.import_module("core.infrastructure.network.network_scan")
sys.modules[__name__] = _target
