import feedparser
import requests
import re
import json
import os
import markdown  # Necesitas instalar: pip install markdown
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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ============================================
# FUENTES RSS
# ============================================
RSS_FEEDS = {
    "animenewsnetwork": {
        "url": "https://www.animenewsnetwork.com/newsfeed/rss.xml",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Internacional", "Reseñas"]
    },
    "myanimelist": {
        "url": "https://myanimelist.net/news.xml",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "MyAnimeList"]
    },
    "crunchyroll": {
        "url": "https://www.crunchyroll.com/news/rss",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Estrenos", "Crunchyroll"]
    }
}

SCOPES = ['https://www.googleapis.com/auth/blogger']

# ============================================
# AUTENTICACIÓN BLOGGER
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
# BÚSQUEDA DE SINOPSIS EN ANILIST
# ============================================
def buscar_sinopsis_anilist(titulo_anime):
    """
    Busca un anime en AniList y devuelve su sinopsis, géneros y puntaje.
    """
    if not titulo_anime or len(titulo_anime) < 3:
        return None

    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        title { romaji english native }
        description
        genres
        averageScore
        status
        episodes
        startDate { year month day }
        endDate { year month day }
        siteUrl
      }
    }
    """
    variables = {'search': titulo_anime}
    url = 'https://graphql.anilist.co'

    try:
        response = requests.post(
            url,
            json={'query': query, 'variables': variables},
            timeout=15,
            headers={'Content-Type': 'application/json'}
        )
        if response.status_code == 200:
            data = response.json().get('data', {}).get('Media')
            if data:
                # Limpiar descripción de HTML
                desc = data.get('description', '')
                desc_limpia = re.sub(r'<[^>]+>', ' ', desc)
                desc_limpia = re.sub(r'\s+', ' ', desc_limpia).strip()

                return {
                    'titulo': data.get('title', {}).get('romaji', titulo_anime),
                    'titulo_eng': data.get('title', {}).get('english'),
                    'sinopsis': desc_limpia[:500] + '...' if len(desc_limpia) > 500 else desc_limpia,
                    'generos': ', '.join(data.get('genres', [])[:3]),
                    'puntaje': data.get('averageScore'),
                    'episodios': data.get('episodes'),
                    'estado': data.get('status'),
                    'url': data.get('siteUrl')
                }
    except Exception as e:
        print(f"   ⚠️ Error al buscar sinopsis en AniList: {e}")

    return None

# ============================================
# DETECCIÓN DE ANIME EN EL TEXTO
# ============================================
def detectar_anime_en_titulo(titulo):
    """
    Detecta posibles nombres de anime en el título de la noticia.
    """
    # Lista de palabras clave a ignorar (para evitar falsos positivos)
    ignorar = ['switch', 'playstation', 'xbox', 'steam', 'nintendo', 'ventas',
               'mercado', 'argentina', 'mundo', 'juego', 'consola', 'videojuego']

    # Buscar entre paréntesis o comillas
    patrones = [
        r'"([^"]+)"',       # Texto entre comillas
        r'「([^」]+)」',     # Texto entre comillas japonesas
        r'《([^》]+)》',     # Texto entre comillas angulares
        r'\(([^)]+)\)'      # Texto entre paréntesis
    ]

    for patron in patrones:
        matches = re.findall(patron, titulo)
        for match in matches:
            # Si la palabra no está en la lista de ignorar, es un candidato
            if len(match) > 2 and not any(p in match.lower() for p in ignorar):
                return match

    # Si no encuentra entre comillas, busca palabras en mayúscula con al menos 2 palabras
    palabras = titulo.split()
    for i, palabra in enumerate(palabras):
        if palabra[0].isupper() and len(palabra) > 2:
            # Si es una palabra que parece nombre
            if i + 1 < len(palabras) and palabras[i+1][0].isupper():
                return f"{palabra} {palabras[i+1]}"

    return None

# ============================================
# REESCRITURA CON GROQ (IA GRATUITA)
# ============================================
def reescribir_con_groq(titulo, descripcion, fuente_nombre, sinopsis_data=None):
    """
    Usa Groq (Llama 4) para reescribir la noticia con estilo humano, SEO y formato.
    """
    if not GROQ_API_KEY:
        print("   ⚠️ GROQ_API_KEY no configurada. Usando traducción simple.")
        return None

    # SYSTEM PROMPT con directivas Yoast SEO y formato
    system_prompt = """Eres un redactor SEO experto para "Anime Actualidad Argentina", un blog argentino. Tu objetivo es reescribir noticias siguiendo las mejores prácticas de Yoast SEO para mejorar el posicionamiento en buscadores.

**REGLAS DE SEO YOAST (OBLIGATORIAS):**
1.  **Título SEO**: Crea un título atractivo de máximo 60 caracteres que incluya la palabra clave principal.
2.  **Meta Descripción**: Añade al inicio un párrafo de 150-160 caracteres que resuma la noticia e invite a leer más.
3.  **Estructura de Encabezados**: Usa ## H2 para los temas principales y ### H3 para los subtemas.
4.  **Párrafos y Oraciones**: Escribe párrafos cortos (máximo 3-4 líneas) y oraciones de máximo 20 palabras.
5.  **Distribución de la Palabra Clave**: Incluye tu palabra clave principal de forma natural en el título, en el primer párrafo, en al menos un H2 y distribuida en el texto.
6.  **Legibilidad**: Usa listas con viñetas (-) para enumerar datos y palabras de transición para conectar ideas.
7.  **Formato**: Usa **texto en negrita** para resaltar los datos más importantes.

**ESTRUCTURA DEL ARTÍCULO:**
1.  **Introducción**: Responde a la pregunta principal con el dato más impactante.
2.  **Desarrollo**: Profundiza con cifras, nombres, citas o datos relevantes, usando listas y subtítulos.
3.  **Conclusión**: Termina con un llamado a la acción: "¿Qué opinás? Dejanos tu comentario en Anime Actualidad Argentina"

**IMPORTANTE**: Proporciona la salida en formato Markdown para que pueda estructurarse correctamente."""

    # Construir el mensaje del usuario
    user_prompt = f"""Fuente: {fuente_nombre}
Título original: {titulo}
Descripción original: {descripcion[:2000]}

Reescribí esta noticia siguiendo las reglas de SEO y estructura que se te indicaron.

{'' if not sinopsis_data else f'''
**INFORMACIÓN ADICIONAL DEL ANIME RELACIONADO:**
Título: {sinopsis_data['titulo']}
Sinopsis: {sinopsis_data['sinopsis']}
Géneros: {sinopsis_data['generos']}
Puntaje en AniList: {sinopsis_data['puntaje']}/100
URL: {sinopsis_data['url']}

**Importante:** Si la noticia habla de este anime, incluye su sinopsis y datos de manera natural en el desarrollo del artículo.
'''}
"""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1000  # Aumentado para dar más detalle
            },
            timeout=45
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
# CONVERSIÓN DE MARKDOWN A HTML
# ============================================
def convertir_markdown_a_html(texto_markdown):
    """Convierte texto en formato Markdown a HTML para Blogger."""
    if not texto_markdown:
        return ""

    # Configurar extensiones para mejor compatibilidad
    extensions = ['extra', 'codehilite', 'toc', 'nl2br']

    try:
        html = markdown.markdown(texto_markdown, extensions=extensions)
        return html
    except Exception as e:
        print(f"   ⚠️ Error convirtiendo Markdown: {e}")
        # Fallback: reemplazar saltos de línea por <br>
        return texto_markdown.replace('\n', '<br>')

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
# FORMATO DEL POST CON MARKDOWN
# ============================================
def formatear_contenido(texto_markdown, imagen, enlace, fuente):
    # Convertir el markdown a HTML
    texto_html = convertir_markdown_a_html(texto_markdown)

    return f"""
<div style="text-align:center; margin-bottom:20px;">
<img src="{imagen}" alt="Anime" style="max-width:100%;border-radius:10px;"/>
</div>
<p><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
<p><strong>📰 Fuente:</strong> <a href="{enlace}" target="_blank" rel="noopener noreferrer">{fuente['categoria']}</a></p>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<div style="font-size:1.1rem;line-height:1.8;">
{texto_html}
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
                    descripcion = entry.description if 'description' in entry else ''
                    if not descripcion and 'summary' in entry:
                        descripcion = entry.summary

                    # ---- DETECTAR ANIME EN EL TÍTULO ----
                    nombre_anime = detectar_anime_en_titulo(entry.title)
                    sinopsis_data = None
                    if nombre_anime:
                        print(f"   🔍 Detectado anime: {nombre_anime}")
                        sinopsis_data = buscar_sinopsis_anilist(nombre_anime)
                        if sinopsis_data:
                            print(f"   ✅ Sinopsis obtenida para: {sinopsis_data['titulo']}")

                    # ---- REESCRITURA CON GROQ ----
                    texto_markdown = reescribir_con_groq(
                        entry.title,
                        descripcion[:2000],
                        nombre_fuente,
                        sinopsis_data
                    )

                    if texto_markdown:
                        # Traducir título para el post
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                    else:
                        # Respaldo: traducción simple
                        print("   🔄 Usando traducción de respaldo")
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                        texto_markdown = traductor.traducir(descripcion[:500]) if descripcion else "Noticia sin descripción."
                        # Envolver en un párrafo simple
                        texto_markdown = f"**{entry.title}**\n\n{texto_markdown}"

                    # ---- EXTRAER IMAGEN ----
                    imagen = extraer_imagen(entry)

                    # ---- FORMATO ----
                    contenido = formatear_contenido(texto_markdown, imagen, entry.link, config_fuente)

                    # ---- PUBLICAR COMO BORRADOR ----
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

                time.sleep(3)  # Pausa entre noticias

        except Exception as e:
            print(f"❌ Error procesando {nombre_fuente}: {e}")

    print("\n" + "="*60)
    print(f"✅ Proceso completado. {total_publicadas} borradores creados.")
    print("="*60)

if __name__ == "__main__":
    main()
