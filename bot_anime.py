import feedparser
import requests
import re
import json
import os
from datetime import datetime
import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# ============================================
# CONFIGURACIÓN
# ============================================
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # Opcional: si no está, usa traducción simple

RSS_FEEDS = {
    "animenewsnetwork": {
        "url": "https://www.animenewsnetwork.com/all/rss.xml",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Internacional", "Reseñas"]
    },
    "crunchyroll": {
        "url": "https://www.crunchyroll.com/es/news/feed/rss",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Estrenos", "Crunchyroll"]
    }
}

SCOPES = ['https://www.googleapis.com/auth/blogger']

# ============================================
# AUTENTICACIÓN
# ============================================
def autenticar_blogger():
    """Autentica con OAuth 2.0 y devuelve el servicio de Blogger"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('blogger', 'v3', credentials=creds)
    return service


# ============================================
# TRADUCCIÓN (RESPALDO SI NO HAY IA CONFIGURADA)
# ============================================
class Traductor:
    def __init__(self):
        self.api_url = "https://translate.googleapis.com/translate_a/single"

    def traducir(self, texto, target="es"):
        if not texto or len(texto.strip()) < 3:
            return texto
        try:
            params = {
                "client": "gtx", "sl": "auto", "tl": target,
                "dt": "t", "q": texto[:5000]
            }
            response = requests.get(self.api_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                resultado = ""
                for parte in data[0]:
                    if parte[0]:
                        resultado += parte[0]
                return resultado
        except Exception as e:
            print(f"Error en traducción: {e}")
        return texto


# ============================================
# REESCRITURA CON IA (OPCIONAL)
# ============================================
def reescribir_con_ia(titulo, descripcion, fuente_nombre):
    """
    Genera un resumen/artículo ORIGINAL en español a partir de los datos
    de la noticia original, en vez de traducirla literalmente.
    Devuelve None si falla o si no hay API key configurada.
    """
    if not ANTHROPIC_API_KEY:
        return None

    prompt = f"""Sos redactor de un blog de noticias de anime en español (Argentina).
Te paso el título y una descripción breve de una noticia publicada en {fuente_nombre}.

Título original: {titulo}
Descripción original: {descripcion}

Escribí una nota breve (2 párrafos cortos, máximo 120 palabras en total) EN TUS PROPIAS PALABRAS,
contando la misma información pero con redacción propia, tono informativo y cercano para fans de anime.
No inventes datos que no estén en el texto original. No uses comillas textuales.
Devolvé SOLO el texto de la nota, sin título, sin encabezados, sin agregar nada más."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            texto = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return texto.strip() if texto.strip() else None
        else:
            print(f"⚠️ IA respondió con status {response.status_code}, uso traducción de respaldo")
            return None
    except Exception as e:
        print(f"⚠️ Error llamando a la IA: {e}. Uso traducción de respaldo")
        return None


class OptimizadorSEO:
    def __init__(self):
        self.palabras_clave = ["anime", "manga", "estreno", "noticias", "japón", "cultura otaku"]

    def optimizar_titulo(self, titulo_trad):
        if len(titulo_trad) > 65:
            titulo_trad = titulo_trad[:62] + "..."
        return titulo_trad


# ============================================
# EXTRACCIÓN DE IMÁGENES (MEJORADA)
# ============================================
IMAGEN_DEFECTO = "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjHDh8uCDe6OcMJuYQ48ZoDxLDetLv4bCgAesT2hZZrbTlsSVM-vSy-OlGjDnV5W9AE1Y8dapE-ANqUfwyDO2qzqpZRdFQxcAGsOwnYUslcyDuVKI4_zvyi01pgwaQHVqauXTnccYtxd0XLCbq8asfwWCQeXWfrzCJ0xhPiNfSR7zqFbWzy28kxGA"

def extraer_imagen_de_rss(entry):
    """Intenta sacar la imagen directamente de los datos del RSS"""
    if 'media_thumbnail' in entry and entry.media_thumbnail:
        return entry.media_thumbnail[0]['url']
    if 'media_content' in entry and entry.media_content:
        for content in entry.media_content:
            if 'url' in content:
                return content['url']
    if 'links' in entry:
        for link in entry.links:
            if link.get('type', '').startswith('image'):
                return link.href
    if 'content' in entry and entry.content:
        content = entry.content[0].value if isinstance(entry.content, list) else entry.content
        img_match = re.search(r'<img[^>]+src="([^">]+)"', content)
        if img_match:
            return img_match.group(1)
    # A veces la imagen viene en el summary/description como HTML
    if 'summary' in entry:
        img_match = re.search(r'<img[^>]+src="([^">]+)"', entry.summary)
        if img_match:
            return img_match.group(1)
    return None


def extraer_imagen_de_articulo(url_articulo):
    """
    Respaldo: si el RSS no trae imagen, entra a la página del artículo
    y busca la imagen principal (meta og:image), que casi todos los sitios tienen.
    """
    try:
        response = requests.get(
            url_articulo, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BotAnimeNews/1.0)"}
        )
        if response.status_code == 200:
            match = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                response.text
            )
            if match:
                return match.group(1)
    except Exception as e:
        print(f"    ⚠️ No se pudo obtener imagen del artículo: {e}")
    return None


def extraer_imagen(entry):
    imagen = extraer_imagen_de_rss(entry)
    if imagen:
        return imagen

    if hasattr(entry, 'link'):
        imagen = extraer_imagen_de_articulo(entry.link)
        if imagen:
            return imagen

    return IMAGEN_DEFECTO


# ============================================
# FORMATO DEL POST
# ============================================
def formatear_contenido(texto, imagen, enlace, fuente):
    texto_limpio = re.sub(r'<[^>]+>', ' ', texto)
    texto_limpio = re.sub(r'\s+', ' ', texto_limpio).strip()

    return f"""
<div style="text-align:center; margin-bottom:20px;">
<img src="{imagen}" alt="Anime" style="max-width:100%;border-radius:10px;"/>
</div>
<p><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
<p><strong>📰 Fuente:</strong> <a href="{enlace}" target="_blank" rel="noopener noreferrer">{fuente['categoria']}</a></p>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<div style="font-size:1.1rem;line-height:1.8;">
{texto_limpio}
</div>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<p style="text-align:center;font-size:0.9rem;">
<a href="{enlace}" target="_blank" rel="noopener noreferrer" style="color:#f43dce;text-decoration:none;font-weight:bold;">
🔗 Leer noticia completa →
</a>
</p>
<p style="text-align:center;font-size:0.8rem;color:#9a9a9a;">
Publicado automáticamente por Bot de Anime Actualidad Argentina
</p>
"""


# ============================================
# FUNCIÓN PRINCIPAL
# ============================================
def main():
    print("="*60)
    print("🤖 BOT DE AUTOMATIZACIÓN - ANIME ACTUALIDAD ARGENTINA")
    print("="*60)

    if not BLOGGER_BLOG_ID:
        print("❌ Error: BLOGGER_BLOG_ID no está configurado")
        return

    print(f"✅ Blog ID: {BLOGGER_BLOG_ID}")
    print(f"🧠 Reescritura con IA: {'ACTIVADA' if ANTHROPIC_API_KEY else 'desactivada (usando traducción simple)'}")

    if not os.path.exists('credentials.json'):
        print("❌ Error: No se encontró el archivo credentials.json")
        return

    try:
        service = autenticar_blogger()
        print("✅ Autenticación exitosa")
    except Exception as e:
        print(f"❌ Error de autenticación: {e}")
        return

    traductor = Traductor()
    seo = OptimizadorSEO()
    total_publicadas = 0

    for nombre_fuente, config_fuente in RSS_FEEDS.items():
        print(f"\n📰 Procesando: {nombre_fuente}")
        try:
            feed = feedparser.parse(config_fuente["url"])
            print(f"   📡 {len(feed.entries)} noticias encontradas")

            for entry in feed.entries[:5]:
                try:
                    descripcion = entry.description if 'description' in entry else ""

                    # Intentar reescritura con IA; si no hay key o falla, usar traducción
                    texto_final = reescribir_con_ia(entry.title, descripcion[:1500], nombre_fuente)
                    if texto_final:
                        titulo_trad = traductor.traducir(entry.title)  # el título sí conviene traducirlo simple
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                    else:
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                        texto_final = traductor.traducir(descripcion[:500])

                    imagen = extraer_imagen(entry)
                    contenido = formatear_contenido(texto_final, imagen, entry.link, config_fuente)

                    post = {
                        'title': titulo_trad,
                        'content': contenido,
                        'labels': config_fuente['etiquetas'],
                        'status': 'DRAFT'
                    }

                    result = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=post).execute()
                    print(f"   ✅ Borrador creado: {titulo_trad[:50]}...")
                    total_publicadas += 1

                except Exception as e:
                    print(f"   ❌ Error al crear borrador: {e}")

                time.sleep(2)

        except Exception as e:
            print(f"❌ Error procesando {nombre_fuente}: {e}")

    print("\n" + "="*60)
    print(f"✅ Proceso completado. {total_publicadas} borradores creados.")
    print("="*60)


if __name__ == "__main__":
    main()
