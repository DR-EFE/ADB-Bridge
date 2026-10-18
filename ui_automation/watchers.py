"""Módulo de observadores (Watchers) automáticos para popups y diálogos en Android.

Gestiona la detección y cierre automático de ventanas emergentes, diálogos de permisos
y alertas del sistema usando uiautomator2, con soporte para fallback de IA/OCR.
"""

import logging
from typing import List, Callable, Optional, Sequence
import uiautomator2 as u2

logger = logging.getLogger("ui_automation.watchers")

# Textos habituales en botones de diálogo, permisos y confirmaciones del sistema
DEFAULT_POPUP_TEXTS: List[str] = [
    "Permitir",
    "Aceptar",
    "OK",
    "Sí",
    "Continuar",
    "Cerrar",
    "Allow",
    "Accept",
    "No, gracias",
]


def try_ai_fallback(
    d: u2.Device,
    target_texts: Optional[Sequence[str]] = None,
    screenshot_path: Optional[str] = None,
) -> bool:
    """
    Hook de fallback inteligente asistido por IA / OCR.

    Se invoca cuando los selectores nativos de UIAutomator no logran encontrar o interactuar
    con un botón de diálogo (por ejemplo, en interfaces renderizadas con WebView/Canvas,
    elementos con identificadores dinámicos u ofuscados, o diálogos con texto dentro de imágenes).

    Flujo de integración sugerido con IA Local (Ollama / RapidOCR):
      1. Captura de pantalla en memoria:
         `image = d.screenshot(format="pillow")`
      2. Detección de texto y coordenadas con OCR ligero o VLM:
         `boxes = ocr_engine.detect(image, keywords=target_texts)`
      3. Si se localiza el botón, calcular su centro y hacer clic directo en coordenadas físicas:
         `d.click(center_x, center_y)`

    :param d: Dispositivo uiautomator2 activo.
    :param target_texts: Lista de textos que se buscan visualmente.
    :param screenshot_path: Ruta opcional para almacenar la captura si se desea depurar.
    :return: True si la IA resolvió la interacción con éxito, False de lo contrario.
    """
    target_texts = target_texts or DEFAULT_POPUP_TEXTS
    logger.debug(
        "try_ai_fallback convocado para textos: %s (Hook disponible para motor OCR/VLM)",
        target_texts,
    )

    # -------------------------------------------------------------------------
    # NOTA DE INTEGRACIÓN FUTURA CON IA LOCAL:
    # Aquí se puede invocar un motor local como RapidOCR / PaddleOCR o consultar
    # el endpoint de Ollama (ej. llama3.2-vision) pasando d.screenshot().
    # -------------------------------------------------------------------------
    return False


def setup_auto_popup_watcher(
    d: u2.Device,
    target_texts: Optional[Sequence[str]] = None,
    watcher_name: str = "auto_popup",
    start_monitoring: bool = False,
    interval: float = 2.0,
) -> None:
    """
    Registra un Watcher global en uiautomator2 (por defecto "auto_popup") que detecta
    y hace clic automáticamente en botones de diálogo que contengan cualquiera de los textos
    indicados.

    Textos monitoreados por defecto:
    "Permitir", "Aceptar", "OK", "Sí", "Continuar", "Cerrar", "Allow", "Accept", "No, gracias"

    :param d: Dispositivo uiautomator2.
    :param target_texts: Lista personalizada de textos. Si es None, usa DEFAULT_POPUP_TEXTS.
    :param watcher_name: Nombre asignado al watcher (por defecto "auto_popup").
    :param start_monitoring: Si es True, inicia el hilo de monitoreo en segundo plano.
    :param interval: Intervalo en segundos entre cada chequeo en segundo plano.
    """
    texts = list(target_texts) if target_texts is not None else DEFAULT_POPUP_TEXTS
    logger.info("Configurando watcher '%s' para %d términos...", watcher_name, len(texts))

    # Eliminar registros previos con el mismo nombre para evitar duplicaciones
    d.watcher.remove(watcher_name)

    for text in texts:
        # XPath flexible que busca coincidencias parciales por texto o descripción de contenido
        xpath_query = (
            f"//*[contains(@text, '{text}') or contains(@content-desc, '{text}')]"
        )

        def _create_click_callback(target_kw: str):
            def _callback(selector, device: Optional[u2.Device] = None):
                # Algunas versiones de uiautomator2 invocan ``call`` sólo con
                # el selector; otras pasan también el dispositivo. Aceptar
                # ambas firmas evita que el hilo de watchers se detenga con
                # "missing a required argument: device".
                device = device or d
                try:
                    el = selector.get_last_match()
                    if el:
                        logger.info("Watcher '%s': Clic automático en '%s'", watcher_name, target_kw)
                        el.click()
                        return
                except Exception as click_err:
                    logger.warning(
                        "Watcher '%s': Falló clic nativo en '%s': %s. Intentando fallback IA...",
                        watcher_name,
                        target_kw,
                        click_err,
                    )

                # Si el selector nativo falló en concretar el clic, intentamos el hook de IA
                try_ai_fallback(device, target_texts=[target_kw])

            return _callback

        d.watcher(watcher_name).when(xpath_query).call(_create_click_callback(text))

    logger.info("Watcher '%s' configurado exitosamente.", watcher_name)

    if start_monitoring:
        if not d.watcher.running():
            logger.info("Iniciando servicio de watchers en segundo plano (intervalo=%.1fs)...", interval)
            d.watcher.start(interval=interval)


def remove_all_watchers(d: u2.Device) -> None:
    """
    Detiene el monitor de watchers y elimina todos los observadores registrados en el dispositivo.

    :param d: Instancia del dispositivo uiautomator2.
    """
    logger.info("Eliminando todos los watchers del dispositivo...")
    try:
        if d.watcher.running():
            d.watcher.stop()
        d.watcher.remove()
        d.watcher.reset()
        logger.info("Todos los watchers fueron detenidos y eliminados.")
    except Exception as exc:
        logger.warning("Excepción al remover watchers: %s", exc)


class UIWatcherManager:
    """Clase gestora para el ciclo de vida y configuración de observadores en uiautomator2."""

    def __init__(self, device: u2.Device):
        self.d = device
        self._registered_names: List[str] = []

    def setup_auto_popup(
        self,
        target_texts: Optional[Sequence[str]] = None,
        name: str = "auto_popup",
        start_monitoring: bool = False,
    ) -> None:
        """Configura el watcher global 'auto_popup' a través del gestor."""
        setup_auto_popup_watcher(
            self.d,
            target_texts=target_texts,
            watcher_name=name,
            start_monitoring=start_monitoring,
        )
        if name not in self._registered_names:
            self._registered_names.append(name)

    def register_common_watchers(self) -> None:
        """Alias para registrar el conjunto estándar de auto_popup."""
        self.setup_auto_popup(name="auto_popup", start_monitoring=False)

    def register_custom_watcher(
        self,
        name: str,
        target_text: str,
        action: Optional[Callable] = None,
    ) -> None:
        """
        Registra un observador personalizado para un texto o selector específico.

        :param name: Nombre del watcher.
        :param target_text: Texto o fragmento a vigilar.
        :param action: Función callback a ejecutar. Si es None, hace clic.
        """
        xpath_query = f"//*[contains(@text, '{target_text}')]"
        rule = self.d.watcher(name).when(xpath_query)
        if action:
            rule.call(action)
        else:
            rule.click()

        if name not in self._registered_names:
            self._registered_names.append(name)
        logger.info("Observador personalizado registrado: %s (detecta '%s')", name, target_text)

    def start(self, interval: float = 2.0) -> None:
        """Inicia la vigilancia continua en segundo plano."""
        if not self.d.watcher.running():
            logger.info("Iniciando vigilancia de watchers en segundo plano (cada %.1fs)...", interval)
            self.d.watcher.start(interval=interval)

    def stop(self) -> None:
        """Detiene la vigilancia en segundo plano."""
        if self.d.watcher.running():
            logger.info("Deteniendo vigilancia de watchers...")
            self.d.watcher.stop()

    def remove_all(self) -> None:
        """Elimina todos los observadores y resetea el estado."""
        remove_all_watchers(self.d)
        self._registered_names.clear()

    def is_running(self) -> bool:
        """Indica si el hilo de monitoreo de watchers está en ejecución."""
        return self.d.watcher.running()
