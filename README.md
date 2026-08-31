# Dota 2 Config Sync

App de escritorio (Windows) para sincronizar configuración entre varias cuentas de
Steam/Dota 2: reemplaza la carpeta `570` completa (con backup) y genera un
`hero_grid_config.json` con una plantilla **Meta Meta** + tus héroes favoritos
calculados desde OpenDota.

## Estructura

```
main.py                      # punto de entrada
dota_config_sync/
  config.py                  # carga/guarda config.json (incluye la API key)
  paths.py                   # rutas de config y caché (válidas para script y .exe)
  cache.py                   # caché en disco con TTL
  http.py                    # sesión con reintentos + limitador de tasa (OpenDota)
  steam.py                   # detección de Steam, cuentas, perfil y horas
  opendota.py                # héroes, rango, winrate y favoritos
  hero_grid.py               # construcción/guardado del hero grid
  fileops.py                 # reemplazo de la carpeta 570 con backup y progreso
  theme.py / widgets.py / app.py   # interfaz (CustomTkinter)
tests/                       # tests de la lógica pura
```

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuración

La **Steam Web API key** (necesaria para avatares y horas reales) se puede dar de tres formas:

1. Escribiéndola en el campo de la app y pulsando **Guardar** → se persiste en `config.json`.
2. Copiando `config.example.json` a `config.json` y rellenando `steam_api_key`.
3. Variable de entorno `STEAM_API_KEY` (tiene prioridad y no toca disco).

> `config.json` está en `.gitignore`: tu key nunca se versiona.

Consigue una key en <https://steamcommunity.com/dev/apikey>. El rango y el winrate
funcionan sin key (vía OpenDota); solo avatares y horas la necesitan.

## Uso

```powershell
python main.py
```

1. La app detecta Steam y lista tus cuentas.
2. Elige cuenta **origen** y **destino**.
3. **Reemplazar 570**: copia la config completa (hace backup del destino antes; avisa si Dota está abierto).
4. **Generar Hero Grid**: crea Meta Meta + favoritos para la cuenta destino.

## Tests

```powershell
pip install pytest
pytest
```

## Compilar el .exe

```powershell
pip install pyinstaller
pyinstaller DotaConfigSyncByDraenool.spec
```

El ejecutable queda en `dist/DotaConfigSyncByDraenool.exe`. `config.json` y `.cache/`
se crean junto al `.exe` en el primer arranque.
