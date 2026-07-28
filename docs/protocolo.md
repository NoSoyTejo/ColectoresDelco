# Protocolo del colector — según `Comados.doc`

Fuente oficial: [Comados.doc](Comados.doc)  
Texto extraído: [Comados_extracted.txt](Comados_extracted.txt)

## Parámetros serial

| Parámetro | Valor |
|-----------|--------|
| Baud rate | 9600 |
| Data bits | 8 |
| Parity | None (`N`) |
| Stop bits | 1 |
| Terminador | `\r\n` (probar `\r` si no responde) |

Medidores y cabeceras se completan a **12 dígitos** con ceros a la izquierda.

## Flujo habitual

1. `QUIT` — detiene la lectura  
2. Comando de mantenimiento  
3. `WAKE` — inicializa  
4. `ZOOM` — start read  

Si tras `QUIT` responde **lock** → `PWR666666` hasta **unlock**.

## Los 13 comandos del manual

| # | Función | Comando / secuencia |
|---|---------|---------------------|
| 1 | Lectura directa | `R`+medidor(12)+`F031812` (F18 lectura, F12 fecha). Cabecera/display **obsoletos**. |
| 2 | Refresco forzado | `QUIT` → `SGXF`+medidor → `WAKE` → `ZOOM` |
| 3 | Borrar medidor | `QUIT` → `E`+medidor → `WAKE` → `ZOOM` |
| 4 | Agregar medidor | `QUIT` → `A`+medidor[+cabecera] → `WAKE` → `ZOOM`. CP4: `A`+medidor+`00` |
| 5 | Login | `PWR666666` |
| 6 | Versión | `VER` → `CCE16…`=v1, `SLD16…`=v2 |
| 7 | Cantidad medidores | `QUIT` → `O` (IDnum, AMRsw, etc.) |
| 8 | Reiniciar ciclo K | `k=10100000` (AMRsw debe quedar `10100000`) |
| 9 | Forzar lecturas | `QUIT` → `WAKE` → `ZOOM` |
| 10 | Ver horarios | `QUIT` → `i` |
| 11 | Insertar horarios | Colector **sin** horarios previos. Comando de escritura no detallado en el manual. |
| 12 | Fecha/hora | `C`+AAMMDDHHMMSS |
| 13 | Borrar base | `QUIT` (OK) → `DEL` |

### Interpretación lectura directa

Ejemplo: `000023388410 F0318121606 01 000025710040 090527060924 000090059613 01`

- Lectura `000025710040` → **2571.40**  
- Fecha `090527060924` → **2009-05-27 06:09:24**  

### Respuesta comando `i` (horarios)

```
Vendo horarios de pooling
Total de poolings: 3
1 - De 00:00 até 03:59
2 - De 04:00 até 15:59
3 - De 16:00 até  2359
```

## Código

- `src/protocol.py` — comandos y parsers  
- `src/ui/app_window.py` — pestaña Comandos  
- `src/ui/extra_tabs.py` — Reloj / horarios / medidores / carga masiva  
