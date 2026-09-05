---
name: verify-scrapers
description: Verifica contra el sitio real que los parsers de dota2protracker sigan funcionando, y guía la reparación cuando el sitio cambia su estructura. Usar antes de una release, cuando el usuario reporta que la grilla Meta o las grillas D2PT no cargan, o cuando aparece "No se encontró el payload" en el log.
---

# Verificar los scrapers de Dota2ProTracker

`dota_config_sync/dota2protracker.py` no consume una API oficial: extrae un
literal JS embebido en el HTML server-rendered de dota2protracker.com. Eso
funciona bien y es rápido, pero se rompe sin aviso cuando el sitio cambia su
serialización. Ya pasó una vez: el sitio dejó de citar la key `data`
(`"data":{grids:...` → `data:{grids:...`) y el marcador ancló en la comilla.

Los tests unitarios usan fixtures fijos, así que **pasan en verde con el sitio
roto**. La única verificación real es contra el HTML vivo.

## Correr la verificación

```bash
python .claude/skills/verify-scrapers/scripts/check_scrapers.py
```

Descarga las dos fuentes en vivo (salteando la caché en disco), confirma que
cada marcador siga presente, corre los parsers y muestra lo extraído. Sale con
código 1 si algo se rompió.

Interpretá así:

| Salida | Significa |
|---|---|
| `TODO OK` + héroes por posición y 6 grids | Sano |
| `marcador AUSENTE` | El sitio cambió la estructura: reparar el marcador |
| `PARSER ROTO` con marcador presente | Cambió el formato del literal, no el ancla |
| `HTTP 403` | Cloudflare bloqueó esta IP/red, no es un cambio del sitio |
| `FALLO DE RED` | Firewall, proxy o DNS local — probar en otra red antes de tocar código |

## Reparar un marcador roto

1. Bajá la página cruda y buscá el dato por su contenido, no por su estructura
   (los nombres de héroe o los ids sí sobreviven a un cambio de serialización):

   ```bash
   curl -sL -A "DotaConfigSync/5.0" -o /tmp/page.html "https://dota2protracker.com/"
   grep -o '.\{80\}roleName.\{200\}' /tmp/page.html | head -3
   ```

2. Elegí el ancla **más interna que siga siendo única**. Las keys externas son
   las que cambian de citado; anclar en `matches:{configs:` sobrevivió a un
   cambio que rompió `"data":{grids:{matches:{configs:`. Confirmá unicidad:

   ```bash
   grep -c 'TU_MARCADOR' /tmp/page.html      # tiene que dar 1
   ```

3. Actualizá `_ROLES_MARKER` o `_GRIDS_MARKER` en `dota2protracker.py` y
   volvé a correr el script de verificación.

4. Actualizá también el fixture del test correspondiente en
   `tests/test_logic.py` si la forma del literal cambió, no solo el ancla.

## Trampas conocidas del payload

- **Decimales sin cero inicial** (`.527`): válidos en JS, inválidos en JSON
  estricto. `_BARE_DECIMAL` los normaliza; si aparece un campo numérico nuevo
  con otro formato (notación científica, por ejemplo), va a fallar acá.
- **Keys sin comillas**: `_BAREWORD_KEY` las agrega. El regex exige que la key
  venga después de `{` o `,`, así que no toca texto adentro de strings.
- **El marcador de roles incluye `[`**, el de grids no. Cada parser hace su
  propio bracket-matching; no unifiques sin revisar ese detalle.
- El fallback es silencioso por diseño: si el scrape falla, el hero grid se
  genera igual sin esas pestañas. Por eso un scraper roto solo se nota en el
  log, o cuando el usuario avisa que "no carga la grilla".
