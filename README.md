# Proyecto Colectores

Software de escritorio Windows para interrogar colectores conectados por cable **RS232 → USB** (puerto `COMx`). Reemplazo propio de herramientas tipo remote-com.

## Requisitos

- Windows 10/11
- **Python 3.14** (la más reciente instalada en el equipo; no usar 3.8)
- Cable/adaptador USB-serial y driver instalado (FTDI, Prolific, CH340, etc.)
- Colector encendido y cableado

## Instalación

```powershell
cd C:\Users\DELCOCHILE\Desktop\ProyectoColectores
py -3.14 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> Si `python` apunta a una versión antigua, use siempre el venv activado o `.\venv\Scripts\python.exe`.
> Para crear el entorno: `py -3.14 -m venv venv` (no `python -m venv` si el PATH prioriza 3.8).

## Uso

```powershell
.\run.ps1
```

O manualmente:

```powershell
.\venv\Scripts\Activate.ps1
python src\main.py
```

1. Conectar el cable USB → verificar que aparece un `COMx` en **Refrescar**
2. Elegir puerto y baud rate (ver `docs/protocolo.md`)
3. **Conectar**
4. Usar los botones del panel o escribir un comando y **Enviar**
5. Revisar respuestas en el historial (`TX` = enviado, `RX` = recibido)

La configuración se guarda en `config/settings.json`. Cada sesión deja un archivo en `logs/`.

## Ejecutable para producción (sin instalar Python)

```powershell
.\scripts\build_exe.ps1
```

Genera `dist\ProyectoColectores\ProyectoColectores.exe`. Copie esa carpeta completa a los PCs de campo (junto con el driver USB-serial).

## Estructura

```
ProyectoColectores/
  config/settings.json   # puerto/baud persistentes
  logs/                  # historial por sesión (soporte)
  docs/                  # protocolo y manual
  scripts/build_exe.ps1  # empaquetar .exe
  run.ps1                # arranque rápido en desarrollo
  src/
    main.py
    app_paths.py         # rutas dev / .exe
    session_log.py       # log a archivo
    serial_client.py
    protocol.py
    ui/app_window.py
  requirements.txt
```

## Ensayo con hardware

1. Administrador de dispositivos → Puertos (COM y LPT) → anotar `COMx`
2. Abrir la app, seleccionar ese puerto
3. Probar un comando documentado (versión o lectura)
4. Si no hay respuesta: probar otro baud / fin de línea (`CR`, `LF`, `CR+LF`)

## Fase 2 — comandos documentados

Implementados según `docs/ComandosColectores.doc` y logs RemoteCOM:

- Login, VER, QUIT/WAKE/ZOOM, lectura directa y **T1–T4**
- Pestaña **Reloj**: ajustar hora del colector (`C…`)
- Pestaña **Medidores**: listar/escanear con lecturas T1–T4
- Pestaña **Carga masiva**: importar TXT/CSV y enviar comandos `A…`

## Repositorio

Remoto: `https://github.com/NoSoyTejo/ColectoresDelco.git`
