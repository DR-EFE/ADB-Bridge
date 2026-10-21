# Diagnóstico del dispositivo

`DeviceHealthService` recopila un snapshot best-effort mediante `AdbService`:
batería, temperatura si está expuesta, almacenamiento, RAM, identidad Android,
ruta de red, latencia ADB y tipo de transporte. Datos no disponibles se
registran como `UNSUPPORTED`, `ERROR` o estado global `PARTIAL`; nunca como
cero.

```python
from ui.services.adb_service import AdbService
from ui.services.device_health_service import DeviceHealthService

health = DeviceHealthService(AdbService())
snapshot = health.collect_snapshot("192.168.1.50:5555")
print(snapshot.battery_level, snapshot.storage_free_bytes)

history = health.get_history(snapshot.serial, limit=30)
trends = health.get_trends(snapshot.serial, days=7)
print(trends.trends["adb_latency_ms"].average)
```

Los snapshots se guardan en SQLite en `health_snapshots`, dentro de la base de
datos local configurada para Android Tools. El servicio no necesita GUI ni
requiere root.
