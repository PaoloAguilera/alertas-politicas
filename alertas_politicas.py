#!/usr/bin/env python3
"""
ALERTAS POLÍTICAS — Chile
Revisa prensa nacional, Congreso, Gobierno y (opcional) redes sociales,
y envía cada noticia política nueva a Telegram, sin duplicados.

Modos de uso:
  python alertas_politicas.py            -> una revisión (lo que usa GitHub Actions)
  python alertas_politicas.py --loop     -> revisa cada 3 minutos sin parar (para tu PC)
  python alertas_politicas.py --probar   -> prueba cada fuente y muestra cuántas noticias trae
  python alertas_politicas.py --chat-id  -> muestra el chat_id de quien le escribió al bot

Variables de entorno necesarias:
  TELEGRAM_TOKEN    token del bot (te lo da @BotFather)
  TELEGRAM_CHAT_ID  tu chat_id (o el de un grupo/canal)
"""

import calendar
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests

# ═══════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN — edita libremente esta sección
# ═══════════════════════════════════════════════════════════════════════

# Palabras que hacen que una noticia llegue DESTACADA (🔴 y con sonido).
# Escríbelas en minúscula; no importan los tildes.
PALABRAS_DESTACADAS = [
    "urgencia", "discusion inmediata", "suma urgencia",
    "veto", "acusacion constitucional", "interpelacion",
    "cambio de gabinete", "renuncia", "tribunal constitucional",
]

# Si es False, las noticias NO destacadas llegan en silencio (sin sonido).
SONIDO_EN_TODAS = True

# Noticias más antiguas que esto se ignoran (evita avalanchas tras una pausa).
HORAS_MAXIMAS = 3

# Máximo de mensajes por revisión (protección contra inundaciones).
MAXIMO_POR_REVISION = 40

# Similitud de títulos (0 a 1) para considerar que dos medios cuentan la misma noticia.
UMBRAL_DUPLICADO = 0.55

# Términos que identifican una noticia como política (para fuentes generales).
TERMINOS_POLITICOS = [
    "gobierno", "la moneda", "presidente", "presidenta", "mandatario",
    "ministro", "ministra", "ministerio", "subsecretari", "seremi", "delegad",
    "diputad", "senador", "senado", "camara", "congreso", "parlamentari",
    "bancada", "comision de", "proyecto de ley", "ley ", "reforma", "indicacion",
    "tribunal constitucional", "contraloria", "fiscal nacional",
    "partido republicano", "partido socialista", "partido comunista", "oposicion", "oficialismo", "coalicion", "republicano",
    "udi", "renovacion nacional", "evopoli", "frente amplio", "comunista",
    "democracia cristiana", "ppd", "partido liberal",
    "eleccion", "electoral", "servel", "primarias", "candidat", "alcalde",
    "gobernador", "cadem", "encuesta", "segpres", "interpelacion",
    "acusacion constitucional", "veto", "urgencia", "gabinete", "vocero",
]

# Dominios de medios chilenos aceptados cuando la noticia viene de Google News.
MEDIOS_CHILENOS = [
    "latercera.com", "emol.com", "biobiochile.cl", "cooperativa.cl",
    "elmostrador.cl", "ex-ante.cl", "ciperchile.cl", "df.cl", "24horas.cl",
    "t13.cl", "cnnchile.com", "meganoticias.cl", "chvnoticias.cl",
    "adnradio.cl", "eldinamo.cl", "ellibero.cl", "theclinic.cl",
    "eldesconcierto.cl", "lanacion.cl", "lasegunda.com", "elmercurio.com",
    "lun.com", "radioagricultura.cl", "tvn.cl", "publimetro.cl",
    "interferencia.cl", "elsiglo.cl", "camara.cl", "senado.cl", "gob.cl",
    "bcn.cl", "elcomercio.cl", "eldesconcierto.cl", "radiouchile.cl",
    "diarioconstitucional.cl", "pauta.cl", "tele13radio.cl", "mega.cl",
    # Regionales — Valparaíso
    "mercuriovalpo.cl", "estrellavalpo.cl", "soychile.cl", "soyvalparaiso.cl",
]

def _google_news(consulta):
    q = quote_plus(f"{consulta} when:2h")
    return f"https://news.google.com/rss/search?q={q}&hl=es-419&gl=CL&ceid=CL:es-419"

# Cada fuente: (nombre, url, ¿es_politica_siempre?)
#   True  -> toda noticia del feed se envía (secciones de política).
#   False -> solo se envían las que contengan TERMINOS_POLITICOS.
FUENTES = [
    # Google News: cubre Emol, BioBío, T13, CNN Chile, etc. (filtrado a medios chilenos)
    ("Google News · Gobierno", _google_news('"La Moneda" OR ministro OR ministra OR subsecretario OR "vocero de gobierno"'), True),
    ("Google News · Congreso", _google_news('diputados OR senadores OR "Cámara de Diputados" OR Senado OR "proyecto de ley" OR "comisión de"'), True),
    ("Google News · Partidos", _google_news('"Partido Republicano" OR UDI OR "Renovación Nacional" OR Evópoli OR "Frente Amplio" OR "Partido Comunista" OR "Partido Socialista" OR "Democracia Cristiana" OR oposición OR oficialismo'), True),
    ("Google News · Política Chile", _google_news('política Chile'), True),

    # Regionales — Valparaíso (diarios generales: solo pasan las noticias políticas)
    ("Google News · El Mercurio de Valparaíso", _google_news("site:mercuriovalpo.cl"), False),
    ("Google News · La Estrella de Valparaíso", _google_news("site:estrellavalpo.cl"), False),
    ("Google News · Soy Valparaíso", _google_news("site:soychile.cl Valparaíso"), False),

    # Feeds directos de medios (si alguno deja de funcionar, se omite solo)
    ("La Tercera · Política", "https://www.latercera.com/arc/outboundfeeds/rss/category/politica/?outputType=xml", True),
    ("La Tercera · Nacional", "https://www.latercera.com/arc/outboundfeeds/rss/category/nacional/?outputType=xml", False),
    ("El Mostrador", "https://www.elmostrador.cl/feed/", False),
    ("Ex-Ante", "https://www.ex-ante.cl/feed/", False),
    ("CIPER", "https://www.ciperchile.cl/feed/", False),
    ("El Líbero", "https://ellibero.cl/feed/", False),
    ("El Dínamo", "https://www.eldinamo.cl/feed/", False),
    ("The Clinic", "https://www.theclinic.cl/feed/", False),
    ("El Desconcierto", "https://www.eldesconcierto.cl/feed/", False),
    ("Diario Constitucional", "https://www.diarioconstitucional.cl/feed/", False),
]

# Redes sociales (opcional). Agrega cuentas según necesites.
# Bluesky: cualquier perfil público tiene RSS en https://bsky.app/profile/<usuario>/rss
CUENTAS_BLUESKY = [
    # "latercera.com",
]
# YouTube: RSS por canal, https://www.youtube.com/feeds/videos.xml?channel_id=<ID>
CANALES_YOUTUBE = [
    # ("Senado TV", "UCxxxxxxxxxxxxxxxxxxxxxx"),
]

ARCHIVO_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado_alertas.json")

# ═══════════════════════════════════════════════════════════════════════
#  (No necesitas tocar nada desde aquí hacia abajo)
# ═══════════════════════════════════════════════════════════════════════

UA = {"User-Agent": "Mozilla/5.0 (AlertasPoliticasChile/1.0)"}
PALABRAS_VACIAS = set(
    "el la los las un una unos unas de del al a en y o u que por para con sin se su sus "
    "es son fue ser lo le les como mas más pero tras ante sobre entre hasta desde este esta "
    "estos estas ese esa no si ya muy tambien cuando donde quien".split()
)


def _re_terminos():
    return re.compile("|".join(r"\b" + re.escape(t) for t in TERMINOS_POLITICOS))


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto.lower()).strip()


def palabras(titulo):
    return {p for p in re.findall(r"[a-z0-9]+", normalizar(titulo)) if p not in PALABRAS_VACIAS and len(p) > 2}


def similitud(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


RE_POLITICA = _re_terminos()


def todas_las_fuentes():
    fuentes = list(FUENTES)
    for cuenta in CUENTAS_BLUESKY:
        fuentes.append((f"Bluesky · @{cuenta}", f"https://bsky.app/profile/{cuenta}/rss", False))
    for nombre, canal in CANALES_YOUTUBE:
        fuentes.append((f"YouTube · {nombre}", f"https://www.youtube.com/feeds/videos.xml?channel_id={canal}", False))
    return fuentes


def leer_feed(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return feedparser.parse(r.content)


def fecha_item(item):
    for campo in ("published_parsed", "updated_parsed"):
        t = item.get(campo)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def extraer(nombre_fuente, url, siempre_politica):
    """Devuelve una lista de noticias normalizadas de una fuente."""
    feed = leer_feed(url)
    es_google = "news.google.com" in url
    noticias = []
    for it in feed.entries:
        titulo = html.unescape(it.get("title", "")).strip()
        enlace = it.get("link", "")
        medio = nombre_fuente
        if es_google:
            src = it.get("source") or {}
            medio = src.get("title") or medio
            dominio = urlparse(src.get("href", "")).netloc.lower().removeprefix("www.")
            if not any(dominio == d or dominio.endswith("." + d) for d in MEDIOS_CHILENOS):
                continue
            # Google agrega " - Medio" al final del título
            if medio and titulo.endswith(" - " + medio):
                titulo = titulo[: -len(" - " + medio)].strip()
        elif not nombre_fuente.startswith(("Bluesky", "YouTube")):
            medio = nombre_fuente.split(" · ")[0]
        resumen = re.sub("<[^>]+>", " ", it.get("summary", ""))
        if not siempre_politica:
            texto = normalizar(f"{titulo} {resumen}") + " "
            if not RE_POLITICA.search(texto):
                continue
        if not titulo or not enlace:
            continue
        noticias.append({
            "id": it.get("id") or enlace,
            "titulo": titulo,
            "enlace": enlace,
            "medio": medio,
            "fecha": fecha_item(it),
        })
    return noticias


def cargar_estado():
    try:
        with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def guardar_estado(estado):
    limite = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    estado["vistos"] = {k: v for k, v in estado["vistos"].items() if v > limite}
    estado["titulos"] = [t for t in estado["titulos"] if t[1] > limite][-3000:]
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=0)


def enviar_telegram(texto, con_sonido=True):
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    for intento in range(3):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": not con_sonido,
            },
            timeout=20,
        )
        if r.status_code == 429:  # Telegram pide esperar
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("Telegram rechazó el mensaje varias veces")


def formatear(n, destacada):
    hora = ""
    if n["fecha"]:
        local = n["fecha"].astimezone(timezone(timedelta(hours=-3)))
        hora = f" · {local:%H:%M}"
    icono = "🔴" if destacada else "🏛"
    return (
        f"{icono} <b>{html.escape(n['titulo'])}</b>\n"
        f"<i>{html.escape(n['medio'])}{hora}</i>\n"
        f"{html.escape(n['enlace'])}"
    )


def revisar(enviar=True):
    estado = cargar_estado()
    primera_vez = estado is None
    if primera_vez:
        estado = {"vistos": {}, "titulos": []}

    ahora = datetime.now(timezone.utc)
    corte = ahora - timedelta(hours=HORAS_MAXIMAS)
    candidatas, errores = [], []

    for nombre, url, siempre in todas_las_fuentes():
        try:
            candidatas.extend(extraer(nombre, url, siempre))
        except Exception as e:  # una fuente caída no detiene el resto
            errores.append(f"{nombre}: {e}")

    candidatas.sort(key=lambda n: n["fecha"] or ahora)
    titulos_previos = [(set(t[0].split()), t[1]) for t in estado["titulos"]]
    destacadas_norm = [normalizar(p) for p in PALABRAS_DESTACADAS]
    nuevas = []

    for n in candidatas:
        if n["id"] in estado["vistos"]:
            continue
        estado["vistos"][n["id"]] = ahora.timestamp()
        if n["fecha"] and n["fecha"] < corte:
            continue
        p = palabras(n["titulo"])
        if any(similitud(p, previo) >= UMBRAL_DUPLICADO for previo, _ in titulos_previos):
            continue  # la misma noticia ya llegó desde otro medio
        titulos_previos.append((p, ahora.timestamp()))
        estado["titulos"].append([" ".join(sorted(p)), ahora.timestamp()])
        nuevas.append(n)

    if primera_vez:
        guardar_estado(estado)
        if enviar:
            enviar_telegram(
                "✅ <b>Alertas políticas activadas.</b>\n"
                f"Vigilando {len(todas_las_fuentes())} fuentes. "
                "Desde ahora recibirás cada noticia política nueva."
            )
        print(f"Primera ejecución: {len(nuevas)} noticias marcadas como vistas (no se enviaron).")
        return

    enviadas = 0
    for n in nuevas[:MAXIMO_POR_REVISION]:
        texto_norm = normalizar(n["titulo"])
        destacada = any(p in texto_norm for p in destacadas_norm)
        if enviar:
            enviar_telegram(formatear(n, destacada), con_sonido=destacada or SONIDO_EN_TODAS)
            time.sleep(1.1)
        enviadas += 1

    guardar_estado(estado)
    print(f"{ahora:%Y-%m-%d %H:%M} UTC — {len(candidatas)} revisadas, {enviadas} enviadas.")
    for e in errores:
        print("  ⚠", e)


def probar():
    print("Probando fuentes...\n")
    for nombre, url, siempre in todas_las_fuentes():
        try:
            n = extraer(nombre, url, siempre)
            print(f"  ✔ {nombre}: {len(n)} noticias políticas")
            for x in n[:2]:
                print(f"       – {x['titulo'][:90]}  [{x['medio']}]")
        except Exception as e:
            print(f"  ✘ {nombre}: {e}")


def mostrar_chat_id():
    token = os.environ["TELEGRAM_TOKEN"]
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    vistos = set()
    for u in r.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat and chat["id"] not in vistos:
            vistos.add(chat["id"])
            print(f"chat_id: {chat['id']}   ({chat.get('title') or chat.get('first_name', '')})")
    if not vistos:
        print("No hay mensajes. Escríbele cualquier cosa a tu bot en Telegram y vuelve a ejecutar.")


if __name__ == "__main__":
    if "--probar" in sys.argv:
        probar()
    elif "--chat-id" in sys.argv:
        mostrar_chat_id()
    elif "--loop" in sys.argv:
        while True:
            try:
                revisar()
            except Exception as e:
                print("Error:", e)
            time.sleep(180)
    else:
        revisar()
