# Scripts Documentation

## health_check.py

Sonda de salud del despliegue real, desde afuera y sin sesión. La corre el
workflow **Fixbot** (`.github/workflows/fixbot.yml`) cada 6 horas y también se
puede correr a mano.

### What It Does

- Pega contra la URL pública del despliegue y afirma sólo lo observable sin
  credenciales: que el túnel de Cloudflare llega, que `/login` carga, que el
  proxy Next→FastAPI alcanza la API, que SurrealDB responde, que la
  contraseña de la app sigue activada y que las rutas privadas de la API
  siguen devolviendo 401.
- **Cero secretos**: no usa API keys ni la contraseña de la app, y no intenta
  iniciar sesión (golpear el login cada pocas horas desde un runner sería
  indistinguible de un ataque de fuerza bruta contra la propia app).
- Sólo biblioteca estándar: se corre con el `python3` del sistema, sin
  `uv sync`. Eso es deliberado — el job que la ejecuta en CI no instala ni una
  dependencia de terceros.

### Sistema de cascada

Cada chequeo declara de quién depende. Si el padre no pasa, el hijo no se
ejecuta: se reporta como *no evaluado* apuntando a la causa raíz. Un túnel
caído deja **un** rojo (la causa) y el resto explicado, en vez de 14 rojos
entre los que hay que adivinar. La cascada además separa las tres formas de
"está caído", que se arreglan distinto:

1. el nombre no resuelve → el quick tunnel murió y **la URL cambió**;
2. Cloudflare responde 502/503/504 → el túnel vive, se cayó el contenedor;
3. la app responde pero un componente adentro falla (SurrealDB, el proxy, la
   contraseña, el gate de la API).

### Usage

```bash
# Con el Makefile (recomendado)
ON_HEALTH_URL=https://tu-tunel.trycloudflare.com make health

# O directo, con el python del sistema
python3 scripts/health_check.py --url=https://tu-tunel.trycloudflare.com

# Guardando el informe en markdown (lo que el Fixbot pega en el issue)
python3 scripts/health_check.py --report=informe-salud.md
```

### Exit Codes

| Código | Significado |
|---|---|
| `0` | todos los chequeos críticos pasaron |
| `1` | hay algo roto (o un chequeo crítico que no se pudo medir) |
| `2` | no hay URL configurada — falta `--url` o `ON_HEALTH_URL` |

El `2` existe para que el Fixbot no confunda "no lo chequeé" con "está todo
bien": en el issue aparece como *sin configurar*, no como verde ni como rojo.

### Configuración en GitHub (dos pasos manuales)

1. **Crear el secreto de repo `ON_HEALTH_URL`** en *Settings → Secrets and
   variables → Actions → New repository secret*, con la URL del túnel de
   Cloudflare. Va como secreto y no hardcodeada porque **este repo es
   público**: la URL es, en la práctica, la ubicación de un servidor con
   documentos personales detrás de una sola contraseña. Por el mismo motivo la
   sonda enmascara el host (`https://***.trycloudflare.com`) en el informe que
   termina en un issue público.
2. **Actualizar ese secreto cada vez que el túnel se reinicie.** Es un *quick
   tunnel* gratis, no está fijado a una cuenta, así que al reiniciarse cambia
   de URL. Para recuperar la nueva:

   ```bash
   ssh <vm>
   sudo journalctl -u cloudflared -n 20 | grep trycloudflare.com
   ```

   Mientras el secreto apunte a la URL vieja, el primer chequeo de la cascada
   falla con ese diagnóstico exacto y los demás quedan como no evaluados.

### Notes

- Toda expectativa del script se verificó primero a mano contra el despliegue
  real: si un chequeo falla, falla porque la app cambió, no porque la
  expectativa fuera inventada.
- **En este repo la rama por defecto es `main`, pero el trabajo vive en
  `master`.** Los workflows con `schedule` corren SOLO desde la rama por
  defecto, así que mientras siga así el Fixbot no se dispara ni aparece su
  botón *Run workflow*. Arreglo recomendado (un clic): *Settings → General →
  Default branch → `master`*; de paso los tests del repo empiezan a correr
  sobre el trabajo propio, que hoy tampoco pasa (`test.yml` también se dispara
  sólo en `main`, y este fork no tiene ni una corrida en su historial).
- GitHub deshabilita los workflows programados en los repos que son fork, y
  también en los repos públicos tras 60 días sin actividad. Si el Fixbot deja
  de correr solo, ése es el motivo — el botón *Run workflow* de la pestaña
  Actions es la salida manual.

## export_docs.py

Consolidates markdown documentation files for use with ChatGPT or other platforms with file upload limits.

### What It Does

- Scans all subdirectories in the `docs/` folder
- For each subdirectory, combines all `.md` files (excluding `index.md` files)
- Creates one consolidated markdown file per subdirectory
- Saves all exported files to `doc_exports/` in the project root

### Usage

```bash
# Using Makefile (recommended)
make export-docs

# Or run directly with uv
uv run python scripts/export_docs.py

# Or run with standard Python
python scripts/export_docs.py
```

### Output

The script creates `doc_exports/` directory with consolidated files like:

- `getting-started.md` - All getting-started documentation
- `user-guide.md` - All user guide content
- `features.md` - All feature documentation
- `development.md` - All development documentation
- etc.

Each exported file includes:
- A main header with the folder name
- Section headers for each source file
- Source file attribution
- The complete content from each markdown file
- Visual separators between sections

### Example Output Structure

```markdown
# Getting Started

This document consolidates all content from the getting-started documentation folder.

---

## Installation

*Source: installation.md*

[Full content of installation.md]

---

## Quick Start

*Source: quick-start.md*

[Full content of quick-start.md]

---
```

### Notes

- The `doc_exports/` directory is gitignored and safe to regenerate anytime
- Index files (`index.md`) are automatically excluded
- Files are sorted alphabetically for consistent output
- The script handles subdirectories only (ignores files in the root `docs/` folder)
