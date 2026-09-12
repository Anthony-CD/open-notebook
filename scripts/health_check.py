#!/usr/bin/env python3
"""Chequeo de salud del despliegue real de Open Notebook.

Lo corre el workflow "Fixbot" (`.github/workflows/fixbot.yml`) cada pocas
horas. Tambien sirve a mano:

    make health                                   # usa $ON_HEALTH_URL
    python3 scripts/health_check.py --url=https://mi-tunel.trycloudflare.com
    python3 scripts/health_check.py --report=informe.md

Solo biblioteca estandar, igual que `scripts/check_md_links.py`: se corre con
el `python3` del sistema, sin `uv sync` ni `npm ci`. Eso no es una comodidad,
es parte del diseno de seguridad — el job que ejecuta esta sonda no instala
ni una dependencia de terceros (ver el encabezado del workflow).

Filosofia (la misma del fixbot hermano de Finanzas y Polyglot)
--------------------------------------------------------------
* NO mockea nada. Pega contra la URL que se le pasa y afirma solo lo
  observable desde afuera, sin sesion. Cada expectativa de este archivo se
  verifico primero a mano contra el despliegue real (2026-09-10): si un
  chequeo falla, falla porque la app cambio, no porque la expectativa fuera
  inventada.
* CERO secretos: ni API keys, ni la contrasena de la app, ni cookies. Por eso
  el workflow puede correr con permisos minimos y cualquiera puede correr
  esto sin credenciales.
* NO intenta iniciar sesion ni adivinar la contrasena. Golpear el login cada
  pocas horas desde un runner es indistinguible de fuerza bruta contra la
  propia app.
* El repo es PUBLICO y el informe termina en un issue publico, asi que la URL
  del despliegue (la unica cosa sensible aca, porque es la puerta de entrada
  a documentos personales) se enmascara en todo lo que se escribe a un
  archivo. Ver `enmascarar`.

Sistema de cascada
------------------
Cada chequeo declara de quien depende (`depende_de`). Si el padre no paso, el
hijo NO se ejecuta: se reporta como "no evaluado" apuntando a la causa raiz.
Sin esto, un tunel caido pinta en rojo los 14 chequeos y hay que adivinar cual
es el problema real; con esto queda UN rojo — la causa — y el resto explicado.

La cascada ademas separa las tres formas de "esta caido", que se arreglan de
maneras completamente distintas:

  1. el nombre no resuelve / no hay conexion  -> el quick tunnel murio y la
     URL cambio (es gratis y no esta fijado a una cuenta): hay que recuperar
     la nueva con `ssh` a la VM y actualizar el secreto `ON_HEALTH_URL`.
  2. Cloudflare contesta 502/503/504          -> el tunel vive, el origen no:
     se cayo el contenedor en la VM de Oracle.
  3. la app contesta pero algo adentro falla  -> app viva, componente roto
     (SurrealDB, el proxy Next->FastAPI, la contrasena, el gate de la API).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

UA = "open-notebook-fixbot/1.0 (health-check)"
# Techo de lectura: estas rutas devuelven JSON chico o una pagina de login de
# ~20KB. Cualquier cosa mas grande no aporta a un chequeo y no la queremos ni
# en memoria ni en el informe.
MAX_CUERPO = 200_000
TIMEOUT_POR_DEFECTO = 30.0
# La VM es un free tier de Oracle: cuando el worker esta procesando una fuente
# grande, la app responde lenta pero responde. Esto avisa sin marcar fallo.
LATENCIA_AVISO_S = 8.0

CRITICO = "critico"
AVISO = "aviso"

# Se rellenan en main(); declaradas aca para que los chequeos las vean.
BASE = ""
HOST = ""
TIMEOUT = TIMEOUT_POR_DEFECTO


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class _SinRedirects(urllib.request.HTTPRedirectHandler):
    """Los 3xx son parte de lo que se afirma, no algo a seguir."""

    def redirect_request(  # type: ignore[override]
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_SinRedirects)


@dataclass
class Respuesta:
    status: int
    headers: dict[str, str]
    cuerpo: bytes
    segundos: float

    def texto(self) -> str:
        return self.cuerpo.decode("utf-8", errors="replace")

    def como_json(self) -> object:
        return json.loads(self.texto())


def pedir(ruta: str) -> Respuesta:
    """GET sin seguir redirects y con timeout duro.

    Un 4xx o 5xx no es una excepcion aca: es una respuesta que queremos
    afirmar, asi que `HTTPError` se normaliza a `Respuesta`.
    """
    pedido = urllib.request.Request(
        f"{BASE}{ruta}", method="GET", headers={"user-agent": UA}
    )
    arranque = time.monotonic()
    try:
        with _OPENER.open(pedido, timeout=TIMEOUT) as r:
            return Respuesta(
                r.status,
                {k.lower(): v for k, v in r.headers.items()},
                r.read(MAX_CUERPO),
                time.monotonic() - arranque,
            )
    except urllib.error.HTTPError as e:
        return Respuesta(
            e.code,
            {k.lower(): v for k, v in e.headers.items()},
            e.read(MAX_CUERPO),
            time.monotonic() - arranque,
        )


# --------------------------------------------------------------------------
# Enmascarado de la URL
# --------------------------------------------------------------------------
def enmascarar(texto: str) -> str:
    """Saca el host del despliegue de cualquier cosa que se vaya a publicar.

    El informe de esta sonda se pega en un issue de un repo PUBLICO. La URL
    del tunel es, en la practica, la ubicacion de un servidor con documentos
    personales detras de una sola contrasena: publicarla en un issue le
    ahorraria a un atacante el unico paso que hoy no puede automatizar
    (encontrarlo). El host se reemplaza por `***`, conservando el dominio de
    nivel superior — con eso alcanza para saber si el tunel es el de siempre.
    """
    if not HOST:
        return texto
    partes = HOST.split(".")
    visible = "***." + ".".join(partes[-2:]) if len(partes) > 2 else "***"
    return texto.replace(HOST, visible)


# --------------------------------------------------------------------------
# Registro de chequeos
# --------------------------------------------------------------------------
@dataclass
class Chequeo:
    id: str
    nombre: str
    severidad: str
    porque: str
    fn: Callable[[], Optional[str]]
    depende_de: Optional[str] = None


@dataclass
class Resultado:
    chequeo: Chequeo
    estado: str  # "ok" | "fallo" | "no_evaluado"
    problema: Optional[str] = None
    causa: Optional[str] = None


CHEQUEOS: list[Chequeo] = []


def chequeo(
    ident: str,
    nombre: str,
    severidad: str,
    porque: str,
    depende_de: Optional[str] = None,
) -> Callable[[Callable[[], Optional[str]]], Callable[[], Optional[str]]]:
    """Registra un chequeo. La funcion devuelve None si pasa, o el problema."""

    def envolver(fn: Callable[[], Optional[str]]) -> Callable[[], Optional[str]]:
        CHEQUEOS.append(Chequeo(ident, nombre, severidad, porque, fn, depende_de))
        return fn

    return envolver


# --- Nivel 0: se llega? ----------------------------------------------------
@chequeo(
    "tunel",
    "El tunel de Cloudflare llega al servidor",
    CRITICO,
    "Es la unica puerta de entrada: los puertos 8502/5055 de la VM no estan "
    "expuestos a internet a proposito. Si esto falla, no hay app para nadie.",
)
def _tunel() -> Optional[str]:
    try:
        res = pedir("/")
    except urllib.error.URLError as e:
        return (
            f"no se pudo conectar ({enmascarar(str(e.reason))}). Si el nombre no "
            "resuelve, el quick tunnel se reinicio y la URL cambio: recuperala "
            "con `ssh` a la VM + `sudo journalctl -u cloudflared -n 20 | grep "
            "trycloudflare.com` y actualiza el secreto ON_HEALTH_URL del repo"
        )
    except TimeoutError:
        return f"no respondio en {TIMEOUT:.0f}s"
    if res.status in (502, 503, 504):
        return (
            f"Cloudflare respondio {res.status}: el tunel esta vivo pero el "
            "origen no. Se cayo el contenedor en la VM, no el tunel — "
            "revisar con `make status` / `docker compose ps` en la VM"
        )
    if res.status >= 500:
        return f"respondio {res.status} — error del servidor"
    return None


@chequeo(
    "raiz",
    "/ manda a /notebooks",
    AVISO,
    "El destino de la portada. Si cambia no se rompe nada grave, pero es la "
    "primera senal de que el routing del frontend no es el que se desplego.",
    depende_de="tunel",
)
def _raiz() -> Optional[str]:
    res = pedir("/")
    if res.status != 307:
        return f"esperaba 307, recibi {res.status}"
    destino = res.headers.get("location", "")
    if destino != "/notebooks":
        return f'redirige a "{enmascarar(destino)}" en vez de a /notebooks'
    return None


@chequeo(
    "login",
    "La pantalla de login carga",
    CRITICO,
    "Es la unica puerta: si /login no responde 200 con la app dentro, nadie "
    "puede entrar aunque todo lo demas este sano.",
    depende_de="tunel",
)
def _login() -> Optional[str]:
    res = pedir("/login")
    if res.status != 200:
        return f"esperaba 200, recibi {res.status}"
    # Un 200 solo no alcanza: una pagina de error tambien responde 200.
    if "Open Notebook" not in res.texto():
        return "respondio 200 pero el HTML no dice 'Open Notebook' (pagina de error?)"
    return None


@chequeo(
    "latencia",
    "La app responde en tiempo razonable",
    AVISO,
    "La VM es un free tier: cuando el worker procesa una fuente grande la app "
    "se pone lenta pero funciona. Sirve para explicar un 'va lentisimo' sin "
    "tener que entrar a la VM, no para marcar la app como caida.",
    depende_de="login",
)
def _latencia() -> Optional[str]:
    res = pedir("/login")
    if res.segundos > LATENCIA_AVISO_S:
        return f"/login tardo {res.segundos:.1f}s (umbral {LATENCIA_AVISO_S:.0f}s)"
    return None


# --- Nivel 1: el frontend sirve su configuracion ---------------------------
@chequeo(
    "config_front",
    "El frontend publica su configuracion de runtime",
    CRITICO,
    "/config es de donde el navegador saca la URL de la API "
    "(frontend/src/app/config/route.ts). Si se rompe, la app carga y despues "
    "no puede hablar con nada.",
    depende_de="tunel",
)
def _config_front() -> Optional[str]:
    res = pedir("/config")
    if res.status != 200:
        return f"esperaba 200, recibi {res.status}"
    try:
        datos = res.como_json()
    except json.JSONDecodeError:
        return "no devolvio JSON valido"
    if not isinstance(datos, dict) or "apiUrl" not in datos:
        return "el JSON no trae la clave 'apiUrl'"
    api_url = datos["apiUrl"]
    if not isinstance(api_url, str):
        return f"'apiUrl' no es un string, es {type(api_url).__name__}"
    # Vacio = ruta relativa (lo correcto detras del tunel). Si viene absoluta,
    # tiene que apuntar aca: una apiUrl a otro host manda el bearer token del
    # usuario a ese host, que es exactamente el agujero que la validacion de
    # Host de ese route.ts existe para tapar.
    if api_url and urllib.parse.urlparse(api_url).hostname not in (None, HOST):
        return (
            "apiUrl apunta a otro host — el navegador mandaria el token de "
            "sesion ahi; revisar la variable API_URL del contenedor"
        )
    return None


# --- Nivel 2: el proxy Next -> FastAPI y lo que hay detras -----------------
@chequeo(
    "proxy_api",
    "El proxy Next->FastAPI alcanza la API",
    CRITICO,
    "El tunel solo expone el frontend; todo /api/* se reescribe a la FastAPI "
    "en localhost:5055 (frontend/next.config.ts). Si esto falla, la app esta "
    "de pie pero vacia: ni fuentes, ni notas, ni chat.",
    depende_de="tunel",
)
def _proxy_api() -> Optional[str]:
    res = pedir("/api/auth/status")
    if res.status != 200:
        return f"esperaba 200, recibi {res.status}"
    try:
        datos = res.como_json()
    except json.JSONDecodeError:
        return "no devolvio JSON valido (contesto el frontend en vez de la API?)"
    if not isinstance(datos, dict) or "auth_enabled" not in datos:
        return "el JSON no trae 'auth_enabled'"
    return None


@chequeo(
    "auth_prendida",
    "La contrasena de la app sigue activada",
    CRITICO,
    "auth_enabled sale de que OPEN_NOTEBOOK_PASSWORD este definida. Si el "
    "contenedor arranca sin esa variable, la API deja de pedir contrasena y "
    "TODOS los documentos personales quedan abiertos a cualquiera que tenga "
    "la URL. Un 'todo verde' con esto en false seria el peor falso negativo "
    "posible.",
    depende_de="proxy_api",
)
def _auth_prendida() -> Optional[str]:
    datos = pedir("/api/auth/status").como_json()
    if not isinstance(datos, dict):
        return "respuesta inesperada"
    if datos.get("auth_enabled") is not True:
        return (
            "auth_enabled es false! la app esta sirviendo sin contrasena — "
            "falta OPEN_NOTEBOOK_PASSWORD en el entorno del contenedor"
        )
    return None


@chequeo(
    "db",
    "SurrealDB responde",
    CRITICO,
    "dbStatus lo calcula la API con un `RETURN 1` real contra SurrealDB "
    "(api/routers/config.py). Es la dependencia de la que todo cuelga: sin "
    "base no hay notebooks, ni fuentes, ni historial.",
    depende_de="proxy_api",
)
def _db() -> Optional[str]:
    datos = pedir("/api/config").como_json()
    if not isinstance(datos, dict):
        return "respuesta inesperada"
    estado = datos.get("dbStatus")
    if estado != "online":
        return (
            f'dbStatus = "{estado}" (esperaba "online") — el contenedor de '
            "SurrealDB esta caido o no acepta conexiones"
        )
    return None


@chequeo(
    "sin_recon",
    "No filtra la version exacta sin autenticar",
    CRITICO,
    "Hallazgo del pentest de Strix (2026-08-29), ya arreglado: /api/config "
    "esta excluido de la autenticacion a proposito (el frontend necesita "
    "dbStatus antes del login), asi que solo devuelve la version exacta si "
    "quien pregunta ya esta autenticado. Que vuelva a filtrarse significa que "
    "ese arreglo se perdio en un merge con upstream.",
    depende_de="proxy_api",
)
def _sin_recon() -> Optional[str]:
    datos = pedir("/api/config").como_json()
    if not isinstance(datos, dict):
        return "respuesta inesperada"
    version = datos.get("version")
    if version:
        return (
            f'devolvio version="{version}" sin autenticacion — volvio la '
            "regresion que arreglo el commit 054457d"
        )
    return None


# --- Nivel 3: el porton de la API sigue cerrado ----------------------------
# El bloque que mas importa. Aca un 200 NO seria "todo bien": seria que los
# documentos personales de la app quedaron abiertos sin contrasena.
for _ruta in (
    "/api/notebooks",
    "/api/sources",
    "/api/notes",
    "/api/models",
    "/api/settings",
):

    @chequeo(
        f"puerta{_ruta}",
        f"{_ruta} exige contrasena",
        CRITICO,
        f"Si {_ruta} deja de responder 401 sin credenciales, "
        "PasswordAuthMiddleware se rompio (o la ruta se agrego a "
        "excluded_paths por error) y sus datos quedaron publicos.",
        depende_de="proxy_api",
    )
    def _puerta(ruta: str = _ruta) -> Optional[str]:
        res = pedir(ruta)
        if 200 <= res.status < 300:
            return f"respondio {res.status}! esta sirviendo datos sin contrasena"
        if res.status != 401:
            return f"esperaba 401, recibi {res.status}"
        return None


@chequeo(
    "api_no_expuesta",
    "El tunel no expone la API cruda",
    CRITICO,
    "/openapi.json y /docs estan excluidos de la autenticacion en la API, "
    "pero no se llega a ellos porque el tunel apunta al frontend y solo "
    "/api/* se reescribe. Si esto empieza a responder 200, el tunel quedo "
    "apuntando al puerto 5055: se publicaria el mapa completo de la API y el "
    "frontend dejaria de funcionar.",
    depende_de="tunel",
)
def _api_no_expuesta() -> Optional[str]:
    problemas = []
    for ruta in ("/openapi.json", "/docs"):
        res = pedir(ruta)
        if res.status == 200:
            problemas.append(f"{ruta} responde 200")
    return ", ".join(problemas) if problemas else None


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------
def correr() -> list[Resultado]:
    """Corre la cascada: un chequeo cuyo padre no paso no se ejecuta."""
    estados: dict[str, Resultado] = {}
    resultados: list[Resultado] = []

    for c in CHEQUEOS:
        padre = estados.get(c.depende_de) if c.depende_de else None
        if padre is not None and padre.estado != "ok":
            # La causa raiz es la del padre si el padre tampoco se evaluo.
            causa = padre.causa or padre.chequeo.nombre
            r = Resultado(c, "no_evaluado", causa=causa)
        else:
            try:
                problema = c.fn()
            except Exception as e:  # la sonda nunca debe tumbar la corrida
                problema = f"la sonda lanzo {type(e).__name__}: {enmascarar(str(e))}"
            r = (
                Resultado(c, "fallo", problema=enmascarar(problema))
                if problema
                else Resultado(c, "ok")
            )
        estados[c.id] = r
        resultados.append(r)

        if r.estado == "ok":
            icono = "OK  "
        elif r.estado == "fallo":
            icono = "FALL" if c.severidad == CRITICO else "AVIS"
        else:
            icono = "----"
        detalle = ""
        if r.estado == "fallo":
            detalle = f" -- {r.problema}"
        elif r.estado == "no_evaluado":
            detalle = f" -- no evaluado (causa: {r.causa})"
        print(f"[{icono}] {c.nombre}{detalle}")

    return resultados


def _por_severidad(resultados: list[Resultado], severidad: str) -> list[Resultado]:
    return [
        r
        for r in resultados
        if r.estado == "fallo" and r.chequeo.severidad == severidad
    ]


def escribir_informe(ruta: str, resultados: list[Resultado]) -> None:
    """Informe en markdown, apto para pegarse en un issue PUBLICO."""
    criticos = _por_severidad(resultados, CRITICO)
    avisos = _por_severidad(resultados, AVISO)
    saltados = [r for r in resultados if r.estado == "no_evaluado"]

    def linea(r: Resultado) -> str:
        return (
            f"- **{r.chequeo.nombre}** — {r.problema}\n"
            f"  <br>_Por que importa:_ {r.chequeo.porque}"
        )

    bloques = [
        f"**Objetivo:** `{enmascarar(BASE)}`",
        f"**Cuando:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    ]
    if criticos:
        bloques.append(
            f"### Criticos ({len(criticos)})\n\n"
            + "\n".join(linea(r) for r in criticos)
        )
    if avisos:
        bloques.append(
            f"### Avisos ({len(avisos)})\n\n" + "\n".join(linea(r) for r in avisos)
        )
    if saltados:
        bloques.append(
            f"### No evaluados ({len(saltados)})\n\n"
            "No se corrieron porque dependen de algo que ya fallo — arregla la "
            "causa y estos se vuelven a medir solos.\n\n"
            + "\n".join(
                f"- {r.chequeo.nombre} — causa: **{r.causa}**" for r in saltados
            )
        )
    if not criticos and not avisos and not saltados:
        bloques.append(f"Los {len(resultados)} chequeos pasaron.")

    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n\n".join(bloques) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chequeo de salud del despliegue de Open Notebook."
    )
    parser.add_argument("--url", help="URL base del despliegue a chequear")
    parser.add_argument("--report", help="ruta donde escribir el informe markdown")
    args = parser.parse_args()

    # Este proyecto ya perdio una tarde por un UnicodeEncodeError: el worker
    # moria imprimiendo un emoji a la consola cp1252 de Windows. Los mensajes
    # de abajo llevan acentos y guiones largos, asi que la salida se fuerza a
    # UTF-8 tolerante antes de imprimir la primera linea.
    reconfigurar = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigurar):
        reconfigurar(encoding="utf-8", errors="replace")

    crudo = (args.url or os.environ.get("ON_HEALTH_URL") or "").strip()
    if not crudo:
        # A proposito NO hay URL por defecto en el codigo: este repo es
        # publico y la URL del despliegue no se publica. Codigo 2 = "no
        # configurado", distinto de 1 = "encontre algo roto", para que el
        # fixbot no reporte un falso verde ni un falso rojo.
        print(
            "No hay URL para chequear: pasa --url=... o exporta ON_HEALTH_URL.\n"
            "En el repo, el fixbot la toma del secreto ON_HEALTH_URL.",
            file=sys.stderr,
        )
        return 2

    global BASE, HOST, TIMEOUT
    BASE = crudo.rstrip("/")
    partes = urllib.parse.urlparse(BASE)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        print(f"URL invalida: {crudo!r}", file=sys.stderr)
        return 2
    HOST = partes.hostname
    TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT_S", TIMEOUT_POR_DEFECTO))

    # En CI se imprime enmascarada: Actions ya oculta los secretos, pero el
    # informe y los logs son publicos y no dependemos de una sola capa.
    visible = enmascarar(BASE) if os.environ.get("GITHUB_ACTIONS") else BASE
    print(f"Chequeando {visible}\n")

    resultados = correr()

    criticos = _por_severidad(resultados, CRITICO)
    avisos = _por_severidad(resultados, AVISO)
    saltados = [r for r in resultados if r.estado == "no_evaluado"]
    oks = len(resultados) - len(criticos) - len(avisos) - len(saltados)
    print(
        f"\n{oks}/{len(resultados)} OK - {len(criticos)} criticos - "
        f"{len(avisos)} avisos - {len(saltados)} no evaluados - {visible}"
    )

    if args.report:
        escribir_informe(args.report, resultados)

    # Un chequeo saltado tambien cuenta como fallo si era critico: "no lo pude
    # medir" nunca debe reportarse como "esta bien". Un aviso saltado, en
    # cambio, no tumba la corrida (igual que un aviso que falla).
    saltados_criticos = [r for r in saltados if r.chequeo.severidad == CRITICO]
    return 1 if criticos or saltados_criticos else 0


if __name__ == "__main__":
    sys.exit(main())
