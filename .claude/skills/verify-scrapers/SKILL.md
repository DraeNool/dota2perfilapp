---
name: verify-scrapers
description: Scrapers de dota2protracker — verificar contra el sitio vivo y reparar el marcador cuando el sitio cambia. Disparar antes de una release, cuando la grilla Meta o las grillas D2PT "no cargan", o ante "No se encontró el payload" en el log.
---

# Verificar los scrapers de Dota2ProTracker

`dota_config_sync/dota2protracker.py` extrae un literal JS embebido en el HTML
de dota2protracker.com. Los tests usan fixtures fijos, así que **pasan en verde
con el sitio roto**; y el fallback es silencioso por diseño (el hero grid se
genera sin esas pestañas), así que un scraper roto solo asoma en el log o
cuando el usuario avisa. La única verificación real es contra el HTML vivo.

## Verificar

```bash
python .claude/skills/verify-scrapers/scripts/check_scrapers.py
```

| Salida | Significa |
|---|---|
| `TODO OK`, héroes por posición y 6 grids | Sano |
| `marcador AUSENTE` | Cambió la estructura: reparar el marcador |
| `PARSER ROTO` con marcador presente | Cambió el formato del literal, no el ancla |
| `HTTP 403` | Cloudflare bloqueó esta IP/red |
| `FALLO DE RED` | Firewall, proxy o DNS local: probar en otra red antes de tocar código |

## Reparar un marcador

**Hecho** = el script imprime `TODO OK` y `pytest` sigue en verde.

1. Bajá la página cruda y buscá el dato por contenido — nombres de héroe e
   ids sobreviven a un cambio de serialización, la estructura no:

   ```bash
   curl -sL -A "DotaConfigSync/5.0" -o /tmp/page.html "https://dota2protracker.com/"
   grep -o '.\{80\}roleName.\{200\}' /tmp/page.html | head -3
   ```

2. Elegí el ancla **más interna que siga siendo única**: las keys externas son
   las que cambian de citado (`"data":{grids:{matches:{configs:` se rompió;
   `matches:{configs:` sobrevivió). Gate: unicidad.

   ```bash
   grep -c 'TU_MARCADOR' /tmp/page.html      # tiene que dar 1
   ```

3. Actualizá `_ROLES_MARKER` o `_GRIDS_MARKER` en `dota2protracker.py` y
   volvé a correr el script.

4. Si cambió la forma del literal y no solo el ancla, actualizá también el
   fixture en `tests/test_logic.py`.

## Trampas del payload

- Decimales sin cero inicial (`.527`): JS válido, JSON inválido.
  `_BARE_DECIMAL` los normaliza; un formato numérico nuevo (notación
  científica, por ejemplo) va a fallar ahí.
- Keys sin comillas: `_BAREWORD_KEY` las agrega solo tras `{` o `,`, así que
  el texto dentro de strings queda intacto.
