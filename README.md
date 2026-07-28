# Proyecto Colectores

Software de escritorio Windows para interrogar colectores por cable **RS232 → USB** (`COMx`) o por **IP (TCP)**. Reemplazo propio de herramientas tipo remote-com.

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

1. Elegir modo **COM** (cable) o **IP** (TCP)
2. COM: puerto + baud · IP: host + puerto (default 4001)
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
    tcp_client.py      # transporte IP/TCP
    protocol.py
    ui/app_window.py
  requirements.txt
```

## Ensayo con hardware

1. **COM:** Administrador de dispositivos → anotar `COMx` → Refrescar → Conectar
2. **IP:** modo IP → host + puerto TCP → Conectar (requiere colector/gateway en red)
3. Probar un comando documentado (versión o lectura)
4. Si no hay respuesta: probar otro baud / fin de línea (`CR`, `LF`, `CR+LF`) o otro puerto TCP

## Fase 2 — comandos documentados

Implementados según `docs/ComandosColectores.doc` y logs RemoteCOM:

- Login, VER, QUIT/WAKE/ZOOM, lectura directa y **T1–T4**
- Pestaña **Reloj**: ajustar hora del colector (`C…`)
- Pestaña **Medidores**: listar/escanear con lecturas T1–T4
- Pestaña **Carga masiva**: importar TXT/CSV y enviar comandos `A…`

## Repositorio

Remoto: `https://github.com/NoSoyTejo/ColectoresDelco.git`
