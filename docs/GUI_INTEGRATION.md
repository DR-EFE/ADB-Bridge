# Integración GUI Flet

La pestaña **Centro operativo** es una capa de orquestación: no contiene lógica ADB,
backup, sincronización ni salud. Toda acción exige el serial elegido explícitamente en
**Dispositivos**; nunca toma el primer dispositivo disponible.

| Sección | Servicio | Método |
|---|---|---|
| Dispositivos / conexión | `AdbService` | `devices`, `resolve_serial` |
| Centro de respaldos | `BackupService` + cola | `enqueue_profile` |
| Sincronización | `SyncService` + cola | `list_profiles`, `enqueue_sync_job` |
| Salud | `DeviceHealthService` | `collect_snapshot`, `get_trends` |
| Cola inferior | `JobQueueService` | `pause`, `resume`, `cancel`, `retry` |

Las acciones lentas se lanzan en segundo plano y backups/sync se ejecutan mediante la
cola. Los respaldos quedan en la carpeta indicada, por defecto `backups/`.

Esta copia no incluye `automation_service` ni `automation_rules`; la sección lo indica
de forma explícita y no inventa reglas ni historial.
