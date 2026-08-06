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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # ¡USAMOS GROQ EN VEZ DE ANTHROPIC!

RSS_FEEDS = {
    "animenewsnetwork": {
        "url": "https://www.animenewsnetwork.com/newsfeed/rss.xml",  # RSS más confiable
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Internacional", "Reseñas"]
    },
    "crunchyroll": {
        "url": "https://www.crunchyroll.com/news/rss",
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
# TRADUCCIÓN (RESPALDO)
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
# REESCRITURA CON GROQ (IA GRATUITA)
# ============================================
def reescribir_con_groq(titulo, descripcion, fuente_nombre):
    """
    Usa Groq (Llama 4) para reescribir la noticia con estilo humano y SEO.
    Si falla o no hay API key, devuelve None para usar el respaldo.
    """
    if not GROQ_API_KEY:
        print("   ⚠️ GROQ_API_KEY no configurada. Usando traducción simple.")
        return None

    # SYSTEM PROMPT para darle el estilo que queremos
    system_prompt = """Eres un redactor experto en anime para "Anime Actualidad Argentina", un blog argentino.
Tu tarea es REESCRIBIR noticias de anime con estilo humano, atractivo y optimizado para SEO.

REGLAS OBLIGATORIAS:
1. NO copies texto literal. Reinterpretá la noticia con tus propias palabras.
2. Escribí como si le hablaras a un amigo otaku: entusiasta, informado y cercano.
3. Incluye estas palabras clave naturalmente: "anime", "estreno", "temporada", "Argentina", y el nombre del anime.
4. Estructura: 2-3 párrafos con datos clave → CIERRE con llamado a la acción.
5. Si la noticia es sobre un estreno, mencioná fecha y plataforma (Netflix, Crunchyroll, etc.).
6. Al final agregá: "¿Qué opinás? Dejanos tu comentario en Anime Actualidad Argentina"

Ejemplo de tono:
"¡Atención, otakus argentinos! La esperada segunda temporada de 'Jujutsu Kaisen' ya tiene fecha de estreno en Crunchyroll. Prepárense para el 31 de diciembre, porque el caos está por llegar. ¿Ya viste el tráiler? Contanos qué te pareció en los comentarios."

Devolvé SOLO el texto de la nota, sin título, sin encabezados, sin agregar nada más."""

    user_prompt = f"""Fuente: {fuente_nombre}
Título original: {titulo}
Descripción original: {descripcion}

Reescribí esta noticia como si fuera un post ORIGINAL de Anime Actualidad Argentina.
Recordá: estilo humano, con SEO, keywords y tono argentino/otaku."""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",  # Modelo gratuito y potente
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 600
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            texto = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if texto.strip():
                print("   ✅ Reescritura con Groq exitosa")
                return texto.strip()
            else:
                print("   ⚠️ Groq devolvió contenido vacío")
                return None
        else:
            print(f"   ⚠️ Groq respondió con status {response.status_code}")
            return None
    except Exception as e:
        print(f"   ⚠️ Error llamando a Groq: {e}")
        return None

# ============================================
# OPTIMIZADOR SEO PARA TÍTULOS
# ============================================
class OptimizadorSEO:
    def __init__(self):
        self.palabras_clave = ["anime", "manga", "estreno", "noticias", "japón", "cultura otaku"]

    def optimizar_titulo(self, titulo_trad):
        if len(titulo_trad) > 65:
            titulo_trad = titulo_trad[:62] + "..."
        return titulo_trad

# ============================================
# EXTRACCIÓN DE IMÁGENES
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
    if 'summary' in entry:
        img_match = re.search(r'<img[^>]+src="([^">]+)"', entry.summary)
        if img_match:
            return img_match.group(1)
    return None

def extraer_imagen_de_articulo(url_articulo):
    """Respaldo: si el RSS no trae imagen, entra a la página del artículo"""
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
    print(f"🧠 Reescritura con Groq: {'ACTIVADA' if GROQ_API_KEY else 'desactivada (usando traducción simple)'}")

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
                    if not descripcion and 'summary' in entry:
                        descripcion = entry.summary

                    # ---- INTENTAR REESCRITURA CON GROQ ----
                    texto_final = reescribir_con_groq(entry.title, descripcion[:1500], nombre_fuente)
                    
                    if texto_final:
                        # Si Groq funcionó, solo traducimos el título
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                    else:
                        # Respaldo: traducción simple
                        print("   🔄 Usando traducción de respaldo")
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                        texto_final = traductor.traducir(descripcion[:500]) if descripcion else "Noticia sin descripción."

                    # ---- EXTRAER IMAGEN ----
                    imagen = extraer_imagen(entry)
                    
                    # ---- FORMATO ----
                    contenido = formatear_contenido(texto_final, imagen, entry.link, config_fuente)

                    # ---- PUBLICAR COMO BORRADOR ----
                    post = {
                        'title': titulo_trad,
                        'content': contenido,
                        'labels': config_fuente['etiquetas'],
                        'status': 'DRAFT'  # <-- Publicar como borrador para revisar
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
