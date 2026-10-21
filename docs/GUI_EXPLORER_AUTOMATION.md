# GUI: explorador y automatizaciones

El Centro operativo integra los módulos opcionales sin mover su lógica de negocio.
Si se eliminan de una instalación, la aplicación debe degradar la sección mostrando
que el módulo no está disponible; no se ejecutan comandos alternativos desde la UI.

## Explorador

La UI usa `FileExplorerService` con el serial seleccionado explícitamente:

- `list_dir(path, serial)` para navegar una ruta remota.
- `search(root, ExplorerFilter, serial)` para búsquedas glob acotadas.
- `download_paths(serial, paths, local_dir, enqueue_in_job_queue=True)` para descargar
  la selección usando la cola existente.

Los resultados se muestran en una lista acotada y la descarga se ejecuta en segundo
plano. No se implementa un árbol de miniaturas ni búsqueda masiva nueva.

## Automatizaciones

La UI usa `AutomationService` y `automation_rules`:

- `list_rules()` y `list_runs(limit=20)` para el resumen.
- `save_rule(AutomationRule)` para crear una regla simple `device_connected`.
- Las acciones se construyen como `RUN_BACKUP_PROFILE` y/o `RUN_SYNC_PROFILE` usando
  los perfiles ya existentes.

La edición avanzada, ejecución manual y reglas adicionales siguen siendo responsabilidad
del servicio. Los errores se muestran en el estado de la pestaña y no bloquean la UI.
