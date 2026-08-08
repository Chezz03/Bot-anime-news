import feedparser
import requests
import re
import json
import os
import markdown
from datetime import datetime
import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# ============================================
# CONFIGURACIÓN (Variables de entorno)
# ============================================
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # Opcional, mantener como respaldo
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")  # Nueva opción
MODO_PRUEBA = os.environ.get("MODO_PRUEBA", "False").lower() == "true"
PROCESADOS_FILE = "procesados.json"

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
# CONTROL DE DUPLICADOS
# ============================================
def cargar_procesados():
    if os.path.exists(PROCESADOS_FILE):
        with open(PROCESADOS_FILE, 'r', encoding='utf-8') as f:
            try:
                return set(json.load(f))
            except:
                return set()
    return set()

def guardar_procesados(procesados):
    with open(PROCESADOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(procesados), f, ensure_ascii=False, indent=2)

# ============================================
# AUTENTICACIÓN BLOGGER (sin cambios)
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
# BÚSQUEDA DE SINOPSIS EN ANILIST (sin cambios)
# ============================================
def buscar_sinopsis_anilist(titulo_anime):
    """Busca un anime en AniList y devuelve su sinopsis, géneros y puntaje."""
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
# DETECCIÓN DE ANIME EN EL TEXTO (sin cambios)
# ============================================
def detectar_anime_en_titulo(titulo):
    """Detecta posibles nombres de anime en el título de la noticia."""
    ignorar = ['switch', 'playstation', 'xbox', 'steam', 'nintendo', 'ventas',
               'mercado', 'argentina', 'mundo', 'juego', 'consola', 'videojuego']

    patrones = [
        r'"([^"]+)"', r'「([^」]+)」', r'《([^》]+)》', r'\(([^)]+)\)'
    ]

    for patron in patrones:
        matches = re.findall(patron, titulo)
        for match in matches:
            if len(match) > 2 and not any(p in match.lower() for p in ignorar):
                return match

    palabras = titulo.split()
    for i, palabra in enumerate(palabras):
        if palabra[0].isupper() and len(palabra) > 2:
            if i + 1 < len(palabras) and palabras[i+1][0].isupper():
                return f"{palabra} {palabras[i+1]}"
    return None

# ============================================
# SYSTEM PROMPT (tu mismo prompt, sin cambios)
# ============================================
SYSTEM_PROMPT = """
Eres un redactor experto para "Anime Actualidad Argentina", un blog argentino.
Tu tarea es reescribir noticias de anime con un estilo profesional, detallado y atractivo.

REGLAS DE ESTRUCTURA Y ESTILO (OBLIGATORIAS):
1.  **Título**: El título debe ser llamativo, de máximo 60 caracteres, incluyendo el nombre del anime y la palabra clave principal.
2.  **Estructura Fija (con Subtítulos Atractivos)**: Usá la siguiente estructura, pero con títulos creativos (ej. "📢 El anuncio", "🎬 El equipo", "📖 ¿De qué trata?", "📚 Un éxito en papel").
    *   **Introducción**: Anuncio principal, con el dato más impactante.
    *   **Staff de Producción**: Director, estudio, diseño de personajes, guionista, etc.
    *   **Sinopsis Oficial**: Resumen de la trama, con detalles clave.
    *   **Origen y Reconocimientos**: Información del manga, premios, nominaciones, etc.
3.  **Precisión y Detalle**: Incluí TODOS los datos específicos: nombres de personas, estudios, fechas, números de tomos, premios, etc.
4.  **Tono**: Profesional pero cercano, como un periodista especializado que le habla a un público apasionado. Usá emojis para darle dinamismo (📢, 🎬, 📖, 📚).
5.  **Despedida**: Terminá con "¿Qué opinás? Dejanos tu comentario en Anime Actualidad Argentina".

**IMPORTANTE**: Proporcioná la salida en formato Markdown, con la estructura y el tono indicados.
"""

# ============================================
# REESCRITURA CON DEEPSEEK (NUEVO) O GROQ (RESPALDO)
# ============================================
def reescribir_con_deepseek(titulo, descripcion, fuente_nombre, sinopsis_data=None):
    """Usa DeepSeek para reescribir la noticia (más económico y mejor contexto)."""
    if not DEEPSEEK_API_KEY:
        return None

    user_prompt = f"""Fuente: {fuente_nombre}
Título original: {titulo}
Descripción original: {descripcion[:2000]}

Reescribí esta noticia siguiendo las reglas de estructura y estilo que se te indicaron. Desarrollá el contenido con todos los datos específicos y contexto.

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
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1200
            },
            timeout=60
        )
        if response.status_code == 200:
            data = response.json()
            texto = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if texto.strip():
                print("   ✅ Reescritura con DeepSeek exitosa")
                return texto.strip()
        return None
    except Exception as e:
        print(f"   ⚠️ Error en DeepSeek: {e}")
        return None

def reescribir_con_groq(titulo, descripcion, fuente_nombre, sinopsis_data=None):
    """Respaldo con Groq si DeepSeek falla."""
    if not GROQ_API_KEY:
        return None

    user_prompt = f"""Fuente: {fuente_nombre}
Título original: {titulo}
Descripción original: {descripcion[:2000]}

Reescribí esta noticia siguiendo las reglas de estructura y estilo que se te indicaron.

{'' if not sinopsis_data else f'''
**INFORMACIÓN ADICIONAL DEL ANIME RELACIONADO:**
Título: {sinopsis_data['titulo']}
Sinopsis: {sinopsis_data['sinopsis']}
Géneros: {sinopsis_data['generos']}
Puntaje en AniList: {sinopsis_data['puntaje']}/100
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
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1200
            },
            timeout=60
        )
        if response.status_code == 200:
            data = response.json()
            texto = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if texto.strip():
                print("   ✅ Reescritura con Groq exitosa")
                return texto.strip()
        return None
    except Exception as e:
        print(f"   ⚠️ Error en Groq: {e}")
        return None

# ============================================
# CONVERSIÓN DE MARKDOWN A HTML (sin cambios)
# ============================================
def convertir_markdown_a_html(texto_markdown):
    if not texto_markdown:
        return ""
    try:
        html = markdown.markdown(texto_markdown, extensions=['extra', 'codehilite', 'toc', 'nl2br'])
        return html
    except:
        return texto_markdown.replace('\n', '<br>')

# ============================================
# OPTIMIZADOR SEO (sin cambios)
# ============================================
class OptimizadorSEO:
    def __init__(self):
        self.palabras_clave = ["anime", "manga", "estreno", "noticias", "japón", "cultura otaku"]
    def optimizar_titulo(self, titulo_trad):
        if len(titulo_trad) > 65:
            titulo_trad = titulo_trad[:62] + "..."
        return titulo_trad

# ============================================
# EXTRACCIÓN DE IMÁGENES (sin cambios)
# ============================================
IMAGEN_DEFECTO = "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjHDh8uCDe6OcMJuYQ48ZoDxLDetLv4bCgAesT2hZZrbTlsSVM-vSy-OlGjDnV5W9AE1Y8dapE-ANqUfwyDO2qzqpZRdFQxcAGsOwnYUslcyDuVKI4_zvyi01pgwaQHVqauXTnccYtxd0XLCbq8asfwWCQeXWfrzCJ0xhPiNfSR7zqFbWzy28kxGA"

def extraer_imagen_de_rss(entry):
    if 'media_thumbnail' in entry and entry.media_thumbnail:
        return entry.media_thumbnail[0]['url']
    if 'media_content' in entry and entry.media_content:
        for content in entry.media_content:
            if 'url' in content:
                return content['url']
    if 'content' in entry and entry.content:
        content = entry.content[0].value if isinstance(entry.content, list) else entry.content
        img_match = re.search(r'<img[^>]+src="([^">]+)"', content)
        if img_match:
            return img_match.group(1)
    return None

def extraer_imagen_de_articulo(url_articulo):
    try:
        response = requests.get(url_articulo, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', response.text)
            if match:
                return match.group(1)
    except:
        pass
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
# FORMATO DEL POST (sin cambios)
# ============================================
def formatear_contenido(texto_markdown, imagen, enlace, fuente):
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
Publicado por Anime Actualidad Argentina
</p>
"""

# ============================================
# FUNCIÓN PRINCIPAL (MEJORADA)
# ============================================
def main():
    print("="*60)
    print("🤖 BOT DE AUTOMATIZACIÓN - ANIME ACTUALIDAD ARGENTINA")
    print("="*60)

    if not BLOGGER_BLOG_ID:
        print("❌ Error: BLOGGER_BLOG_ID no está configurado")
        return

    print(f"✅ Blog ID: {BLOGGER_BLOG_ID}")
    print(f"🧠 Reescritura con DeepSeek: {'ACTIVADA' if DEEPSEEK_API_KEY else 'desactivada'}")
    print(f"🧠 Reescritura con Groq (respaldo): {'ACTIVADA' if GROQ_API_KEY else 'desactivada'}")
    print(f"🧪 Modo prueba: {'ACTIVADO' if MODO_PRUEBA else 'desactivado'}")

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
    procesados = cargar_procesados()
    total_publicadas = 0

    for nombre_fuente, config_fuente in RSS_FEEDS.items():
        print(f"\n📰 Procesando: {nombre_fuente}")
        try:
            feed = feedparser.parse(config_fuente["url"])
            print(f"   📡 {len(feed.entries)} noticias encontradas")

            for entry in feed.entries[:5]:
                enlace = entry.link

                # Verificar duplicado
                if enlace in procesados:
                    print(f"   ⏭️ Noticia ya procesada: {entry.title[:50]}...")
                    continue

                try:
                    descripcion = entry.description if 'description' in entry else ''
                    if not descripcion and 'summary' in entry:
                        descripcion = entry.summary

                    # Detectar anime
                    nombre_anime = detectar_anime_en_titulo(entry.title)
                    sinopsis_data = None
                    if nombre_anime:
                        print(f"   🔍 Detectado anime: {nombre_anime}")
                        sinopsis_data = buscar_sinopsis_anilist(nombre_anime)
                        if sinopsis_data:
                            print(f"   ✅ Sinopsis obtenida para: {sinopsis_data['titulo']}")

                    # Reescritura (DeepSeek primero, luego Groq)
                    texto_markdown = reescribir_con_deepseek(
                        entry.title, descripcion[:2000], nombre_fuente, sinopsis_data
                    )
                    if not texto_markdown and GROQ_API_KEY:
                        texto_markdown = reescribir_con_groq(
                            entry.title, descripcion[:2000], nombre_fuente, sinopsis_data
                        )

                    if texto_markdown:
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                    else:
                        print("   🔄 Usando traducción de respaldo")
                        titulo_trad = traductor.traducir(entry.title)
                        titulo_trad = seo.optimizar_titulo(titulo_trad)
                        texto_markdown = traductor.traducir(descripcion[:500]) if descripcion else "Noticia sin descripción."
                        texto_markdown = f"**{entry.title}**\n\n{texto_markdown}"

                    imagen = extraer_imagen(entry)
                    contenido = formatear_contenido(texto_markdown, imagen, enlace, config_fuente)

                    # Publicar o simular
                    if MODO_PRUEBA:
                        print(f"   🧪 [PRUEBA] Borrador creado: {titulo_trad[:50]}...")
                    else:
                        post = {
                            'title': titulo_trad,
                            'content': contenido,
                            'labels': config_fuente['etiquetas'],
                            'status': 'DRAFT'
                        }
                        result = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=post).execute()
                        print(f"   ✅ Borrador creado: {titulo_trad[:50]}...")

                    procesados.add(enlace)
                    guardar_procesados(procesados)
                    total_publicadas += 1

                except Exception as e:
                    print(f"   ❌ Error al crear borrador: {e}")

                time.sleep(3)

        except Exception as e:
            print(f"❌ Error procesando {nombre_fuente}: {e}")

    print("\n" + "="*60)
    print(f"✅ Proceso completado. {total_publicadas} borradores creados.")
    print(f"📊 Total de URLs procesadas: {len(procesados)}")
    print("="*60)

if __name__ == "__main__":
    main()
