# Proyecto Conector

Software de escritorio Windows para interrogar colectores conectados por cable **RS232 → USB** (puerto `COMx`). Reemplazo propio de herramientas tipo remote-com.

## Requisitos

- Windows 10/11
- **Python 3.14** (la más reciente instalada en el equipo; no usar 3.8)
- Cable/adaptador USB-serial y driver instalado (FTDI, Prolific, CH340, etc.)
- Colector encendido y cableado

## Instalación

```powershell
cd C:\Users\DELCOCHILE\Desktop\ProyectoConector
py -3.14 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> Si `python` apunta a una versión antigua, use siempre el venv activado o `.\venv\Scripts\python.exe`.
> Para crear el entorno: `py -3.14 -m venv venv` (no `python -m venv` si el PATH prioriza 3.8).

## Uso

```powershell
.\venv\Scripts\Activate.ps1
python src\main.py
```

1. Conectar el cable USB → verificar que aparece un `COMx` en **Refrescar**
2. Elegir puerto y baud rate (ver `docs/protocolo.md`)
3. **Conectar**
4. Escribir un comando y **Enviar**, o usar los atajos (Versión / K)
5. Revisar respuestas en el log (`TX` = enviado, `RX` = recibido)

La última configuración (puerto, baud, fin de línea) se guarda en `config/settings.json`.

## Estructura

```
ProyectoConector/
  config/settings.json   # puerto/baud persistentes
  docs/                  # protocolo y parámetros serial
  src/
    main.py              # arranque
    serial_client.py     # capa COM thread-safe
    protocol.py          # comandos tipados (Fase 2)
    ui/app_window.py     # interfaz
  requirements.txt
```

## Ensayo con hardware

1. Administrador de dispositivos → Puertos (COM y LPT) → anotar `COMx`
2. Abrir la app, seleccionar ese puerto
3. Probar un comando documentado (versión o lectura)
4. Si no hay respuesta: probar otro baud / fin de línea (`CR`, `LF`, `CR+LF`)

## Fase 2 — comandos documentados

Implementados según `docs/ComandosColectores.doc`:

- Login `PWR666666`, `VER`, `QUIT` / `WAKE` / `ZOOM`
- Lectura directa `R…`, display, refresco, agregar/borrar medidor
- Comando K `k=10100000`, cantidad `O`, reloj `C…`, `DEL`

## Repositorio

Remoto: `https://github.com/NoSoyTejo/ColectoresDelco.git`
