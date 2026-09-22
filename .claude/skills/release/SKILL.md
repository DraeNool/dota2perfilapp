---
name: release
description: Publica una versión de Dota 2 Config Sync — bump, gate de calidad, build del .exe, tag, push y Release en GitHub con el binario adjunto.
disable-model-invocation: true
---

# Release de Dota 2 Config Sync

Cada paso es un **gate**: en rojo, el release termina ahí y se reporta qué
falló. Antes de empezar, confirmá con el usuario el número de versión.

**Hecho** = `https://api.github.com/repos/DraeNool/dota2perfilapp/releases/latest`
devuelve el tag nuevo con `DotaConfigSyncByDraenool.exe` entre sus `assets`.

## 1. Gate de calidad

```bash
git status --short      # limpio, o solo los archivos del cambio a publicar
python -m pytest -q
python -m ruff check dota_config_sync/ tests/
```

`mypy` queda fuera del gate: `opendota.py` arrastra errores previos. Si el
cambio toca `dota2protracker.py`, corré antes el skill `verify-scrapers`.

## 2. Bump — el número vive en dos archivos

- `pyproject.toml` → `version = "X.Y.Z"`
- `dota_config_sync/__init__.py` → `__version__ = "X.Y.Z"`

Major cuando el cambio rompe el `config.json` del usuario; minor para
funcionalidad nueva; patch para un fix. Gate: ambos iguales.

```bash
grep -n '^version\|__version__' pyproject.toml dota_config_sync/__init__.py
```

## 3. Build — después del bump

PyInstaller congela el código: un build previo al bump publica el número viejo.

```bash
python -m PyInstaller DotaConfigSyncByDraenool.spec --noconfirm
```

`PermissionError: [WinError 5]` sobre `dist\...exe` = la app está corriendo y
tiene el archivo tomado. Pedí confirmación al usuario antes de cerrarla:

```bash
tasklist | grep -i DotaConfigSync
```

## 4. Commit, tag, push

```bash
git add pyproject.toml dota_config_sync/__init__.py <archivos del cambio>
git commit -m "..."     # estilo: ver git log
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main && git push origin vX.Y.Z
```

## 5. Release en GitHub — el tag es otro objeto

Un tag pusheado deja `releases` vacío. Con `gh` o el MCP de GitHub:

```bash
gh release create vX.Y.Z dist/DotaConfigSyncByDraenool.exe --title "..." --notes "..."
```

Sin ninguno de los dos, el usuario la crea en
`https://github.com/DraeNool/dota2perfilapp/releases/new`: elige el tag
existente, pega las notas y arrastra el `.exe` tal cual como asset. Entregale
las notas listas para pegar y esperá a que confirme antes de dar por **hecho**.

## Notas de release

Redactadas para quien usa la app: qué problema le desaparece. La
implementación va en el commit.

```markdown
## Novedades

- **[Cambio]**: qué cambia para el usuario.

## Instalación

Descargá `DotaConfigSyncByDraenool.exe` de los assets y ejecutalo — portable,
sin instalación. Windows puede mostrar "Editor desconocido" (SmartScreen):
**Más información → Ejecutar de todas formas**.

Requiere Windows + Steam con al menos dos cuentas de Dota 2 usadas en el equipo.
```
