---
name: release
description: Publica una versión nueva de Dota 2 Config Sync — bump de versión, verificación, build del .exe con PyInstaller, commit, tag, push y borrador de release notes. Usar cuando el usuario pida "sacar una release", "publicar versión", "nueva versión" o "subir el exe".
disable-model-invocation: true
---

# Release de Dota 2 Config Sync

Publicar tiene efectos irreversibles (tags y commits públicos), así que este
skill solo se invoca a pedido explícito del usuario. Confirmá el número de
versión antes de empezar y pará ante cualquier paso que falle — nunca sigas
"para probar si el resto anda".

## 1. Verificar que el árbol está listo

```bash
git status --short          # nada inesperado sin commitear
python -m pytest -q
python -m ruff check dota_config_sync/ tests/
```

Tests o lint en rojo = no hay release. `mypy` reporta errores preexistentes en
`opendota.py`; no bloquea, pero no agregues errores nuevos.

Si el cambio toca `dota2protracker.py`, corré antes el skill `verify-scrapers`:
el parser depende de HTML de un tercero y ya se rompió una vez en producción.

## 2. Bump de versión — en DOS archivos

El número vive duplicado y desincronizarlo pasa fácil:

- `pyproject.toml` → `version = "X.Y.Z"`
- `dota_config_sync/__init__.py` → `__version__ = "X.Y.Z"`

Semver: patch para un fix (parser roto, bug), minor para funcionalidad nueva
compatible, major para un cambio que rompe la config del usuario.

Verificá que quedaron iguales:

```bash
grep -n "5\.\|version" pyproject.toml dota_config_sync/__init__.py | grep -i version
```

## 3. Build del .exe — después del bump

El orden importa: PyInstaller congela el código, así que un build previo al
bump publica el número viejo.

```bash
python -m PyInstaller DotaConfigSyncByDraenool.spec --noconfirm
```

**Si falla con `PermissionError: [WinError 5]` sobre `dist\...exe`**, la app
está corriendo y tiene el archivo tomado. Verificá y pedí confirmación al
usuario antes de matar el proceso — puede tener la ventana abierta a propósito:

```bash
tasklist | grep -i DotaConfigSync
```

## 4. Commit, tag y push

```bash
git add pyproject.toml dota_config_sync/__init__.py <archivos del cambio>
git commit -m "..."        # mensaje en el estilo del repo (ver git log)
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

## 5. Crear la Release en GitHub — el tag NO alcanza

Un tag pusheado **no** crea una Release: son objetos distintos. Verificá:

```bash
curl -s "https://api.github.com/repos/DraeNool/dota2perfilapp/releases" | head -c 200
```

Si devuelve `[]`, no hay ninguna Release publicada por más que el tag exista.

Con `gh` CLI o el MCP de GitHub disponible, creala y adjuntá el binario:

```bash
gh release create vX.Y.Z dist/DotaConfigSyncByDraenool.exe --title "..." --notes "..."
```

Sin ninguno de los dos, el usuario tiene que hacerlo a mano en
`https://github.com/DraeNool/dota2perfilapp/releases/new`: elegir el tag ya
existente, pegar las notas, y **arrastrar el `.exe` crudo** (sin comprimir en
zip; GitHub agrega los "Source code (zip/tar.gz)" solo, eso es aparte).
Entregale el texto de las notas listo para pegar.

## 6. Plantilla de release notes

```markdown
## Novedades

- **[Título del cambio]**: qué cambia para el usuario, no qué archivo se tocó.

## Instalación

Descargá `DotaConfigSyncByDraenool.exe` de los assets y ejecutalo — es
portable, no requiere instalación. Windows puede mostrar "Editor desconocido"
(SmartScreen) por no tener firma digital: **Más información → Ejecutar de
todas formas**.

Requiere Windows + Steam con al menos dos cuentas de Dota 2 usadas en el equipo.
```

Escribí las notas desde el punto de vista de quien usa la app: qué problema le
desaparece. Los detalles de implementación van en el mensaje de commit.
