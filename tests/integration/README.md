# Integration tests

Aquí van pruebas que requieren un dispositivo ADB autorizado. Deben usar una
fixture que detecte `adb devices` y aplicar `pytest.mark.skip` si no existe un
dispositivo listo. No deben ejecutarse durante la suite unitaria.
