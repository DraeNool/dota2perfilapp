<p align="center">
  <img src="DotaConfigSyncByDraenool.png" alt="Dota 2 Config Sync" width="96">
</p>

<h1 align="center">Dota 2 Config Sync</h1>

<p align="center">
  Sincronizá tu configuración de Dota 2 entre todas tus cuentas de Steam en un click.
</p>

<p align="center">
  <a href="https://github.com/DraeNool/dota2perfilapp/releases/latest"><img alt="Última versión" src="https://img.shields.io/github/v/release/DraeNool/dota2perfilapp?label=descargar&color=success"></a>
  <img alt="Plataforma" src="https://img.shields.io/badge/plataforma-Windows-0078D6">
  <a href="LICENSE"><img alt="Licencia" src="https://img.shields.io/badge/licencia-MIT-blue"></a>
</p>

---

¿Tenés varias cuentas de Steam para jugar Dota 2 y te cansaste de configurar cada una a
mano? Esta app copia tu configuración (settings, hero grid, autoexec) de una cuenta a
otra, y arma tu hero grid con los héroes en meta actual — todo desde una ventana simple.

## ✨ Qué hace

- **Copia tu configuración completa** de una cuenta a otra: settings de juego, keybinds,
  todo lo que Dota guarda en la carpeta de la cuenta. Hace backup automático antes de
  tocar nada.
- **Arma tu Hero Grid automáticamente**, con tres pestañas listas para usar:
  - **Meta Meta** — plantilla por rol (carry, mid, offlane, soporte).
  - **Favoritos** — tus héroes con mejor rendimiento y tus últimas 20 partidas, calculado
    con tu historial real.
  - **Meta D2PT** — el meta actualizado de [Dota2ProTracker](https://dota2protracker.com),
    bajado en el momento.
- **Detecta tus cuentas de Steam solo** — no hay que tipear SteamID ni buscar carpetas a
  mano.
- **No instala nada**: es un único `.exe` portable, se ejecuta directo.

## 📥 Descargar

1. Andá a [**Releases**](https://github.com/DraeNool/dota2perfilapp/releases/latest) y
   bajá `DotaConfigSyncByDraenool.exe`.
2. Ejecutalo — Windows puede avisar "Editor desconocido" (SmartScreen), es normal en apps
   sin firma digital. Click en **Más información → Ejecutar de todas formas**.
3. Listo, no requiere instalación. Se puede mover a cualquier carpeta o pendrive.

> Requiere Windows y tener Steam instalado con al menos dos cuentas de Dota 2 usadas en
> este equipo.

## 🚀 Cómo usarlo

1. Abrí la app — detecta tus cuentas de Steam automáticamente.
2. Elegí la cuenta **origen** (la que tiene la config que querés copiar) y la cuenta
   **destino**.
3. **Reemplazar configuración**: copia todo de origen a destino (con backup del destino
   por si algo sale mal).
4. **Generar Hero Grid**: crea el grid con Meta + tus favoritos para la cuenta destino.

### Avatares y horas jugadas (opcional)

Para ver avatares reales y horas jugadas necesitás una Steam Web API key (gratis):

1. Conseguila en <https://steamcommunity.com/dev/apikey>.
2. Pegala en el campo correspondiente dentro de la app y guardá.

Sin key, la app funciona igual — rango, winrate y hero grid no la necesitan.

## ❓ Preguntas frecuentes

**¿Es seguro? ¿Toca mis partidas o mi cuenta de Steam?**
No. Solo copia archivos de configuración local (carpeta `570` de Steam) y arma un JSON de
hero grid. No inicia sesión en Steam ni toca tu cuenta online.

**¿Por qué no aparece el nombre de alguna de mis cuentas?**
Puede pasar con cuentas muy viejas que Steam ya no recuerda en su lista de login rápido.
La app intenta rescatarlo de otra fuente interna de Steam; si aún así no aparece, se
muestra igual usando el nombre visible del perfil.

**¿Se puede perder mi configuración actual?**
No — antes de reemplazar nada se hace un backup automático de la cuenta destino.

## 📜 Licencia

[MIT](LICENSE) — usalo, modificalo, compartilo libremente.
