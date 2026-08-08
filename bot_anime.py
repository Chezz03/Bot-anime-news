import feedparser
import requests
import re
import json
import os
import markdown
from datetime import datetime
import time
from bs4 import BeautifulSoup
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
# SCRAPING DEL ARTÍCULO COMPLETO DE ANN
# ============================================
def scrapear_articulo_ann(url):
    """
    Extrae el contenido completo de un artículo de Anime News Network.
    Devuelve un diccionario con: título, contenido, imagen, autor, fecha.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"   ⚠️ Error al obtener el artículo: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # ---- TÍTULO ----
        titulo = soup.find('h1', class_='title')
        if titulo:
            titulo = titulo.get_text(strip=True)
        else:
            titulo = ""

        # ---- CONTENIDO PRINCIPAL ----
        contenido_div = soup.find('div', class_='body')
        if not contenido_div:
            contenido_div = soup.find('div', itemprop='articleBody')

        contenido = ""
        if contenido_div:
            for elemento in contenido_div.find_all(['p', 'ul', 'ol', 'blockquote']):
                if elemento.name in ['ul', 'ol']:
                    items = [li.get_text(strip=True) for li in elemento.find_all('li')]
                    if items:
                        contenido += '\n' + '\n'.join(['- ' + item for item in items]) + '\n'
                else:
                    texto = elemento.get_text(strip=True)
                    if texto:
                        contenido += texto + '\n\n'
        else:
            contenido_div = soup.find('div', class_='news-content')
            if contenido_div:
                for p in contenido_div.find_all('p'):
                    texto = p.get_text(strip=True)
                    if texto:
                        contenido += texto + '\n\n'

        # ---- AUTOR ----
        autor = ""
        autor_meta = soup.find('meta', {'name': 'author'})
        if autor_meta:
            autor = autor_meta.get('content', '')
        if not autor:
            autor_tag = soup.find('span', class_='author')
            if autor_tag:
                autor = autor_tag.get_text(strip=True)

        # ---- FECHA ----
        fecha = ""
        fecha_meta = soup.find('meta', {'property': 'article:published_time'})
        if fecha_meta:
            fecha = fecha_meta.get('content', '')

        # ---- IMAGEN DESTACADA ----
        imagen = ""
        img_tag = soup.find('meta', {'property': 'og:image'})
        if img_tag:
            imagen = img_tag.get('content', '')

        # ---- EXTRAER NOMBRES DE ANIMES ----
        animes_mencionados = []
        for link in soup.find_all('a', class_='article-link'):
            texto = link.get_text(strip=True)
            if texto and len(texto) < 100:
                animes_mencionados.append(texto)
        animes_mencionados = list(set(animes_mencionados))

        # ---- DETECTAR STAFF ----
        staff_roles = {}
        patrones_staff = {
            r'directed by ([^,]+)': 'Director',
            r'written by ([^,]+)': 'Guionista',
            r'produced by ([^,]+)': 'Productor',
            r'animation produced by ([^,]+)': 'Estudio',
            r'studio ([^,]+)': 'Estudio',
            r'designed by ([^,]+)': 'Diseñador',
        }

        for patron, rol in patrones_staff.items():
            match = re.search(patron, contenido, re.IGNORECASE)
            if match:
                staff_roles[rol] = match.group(1).strip()

        # ---- NÚMEROS Y CIFRAS ----
        cifras = re.findall(r'(\d+[,.]?\d*)\s*(millones|unidades|dólares|años|%|¥|yen)', contenido, re.IGNORECASE)

        return {
            'titulo': titulo,
            'contenido': contenido,
            'autor': autor,
            'fecha': fecha,
            'imagen': imagen,
            'animes_mencionados': animes_mencionados[:10],
            'staff': staff_roles,
            'cifras': cifras[:5]
        }
    except Exception as e:
        print(f"   ⚠️ Error scrapeando artículo: {e}")
        return None

# ============================================
# SYSTEM PROMPT - ESTRUCTURA PROFESIONAL
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

EJEMPLO DE TONO Y ESTRUCTURA:
"📢 El nuevo anime de 'Giant Ojō-sama' ya tiene fecha de estreno: enero de 2027. La adaptación del manga de Nikumura Q, producida por Tatsunoko Production y dirigida por Hiroshi Ikehata, promete ser una de las comedias más esperadas de la temporada.

### 🎬 El equipo detrás del proyecto
La serie contará con Hiroshi Ikehata (WITCH WATCH) como director, con Katsuya Oshima como subdirector. Hajime Mitsuda (Food for the Soul) estará a cargo del diseño de personajes y la dirección de animación. La composición de la serie y el guion correrán a cargo de Yū Satō (Azur Lane: Slow Ahead!).

### 📖 ¿De qué trata la historia?
Oriko Fujidō, la heredera de una familia ultra-rica, comienza a crecer hasta alcanzar proporciones gigantescas. Cuando un invasor gigante ataca su pueblo, su mayordomo Sebastian le ofrece una bebida que la transforma en una gigante. Ahora, ella debe proteger su pueblo... aunque el riesgo de destruirlo es inminente.

### 📚 Un éxito en papel
El manga, serializado desde julio de 2020 en Sunday Webry, ya cuenta con 12 tomos recopilatorios. La obra fue nominada en los 'Next Manga Awards' y ocupó el 5º puesto en la encuesta de AnimeJapan sobre el anime más deseado."

**IMPORTANTE**: Proporcioná la salida en formato Markdown, con la estructura y el tono indicados.
"""

# ============================================
# REESCRITURA CON GROQ (IA GRATUITA)
# ============================================
def reescribir_con_groq(titulo, descripcion, fuente_nombre, sinopsis_data=None, articulo_completo=None):
    """
    Usa Groq (Llama 4) para reescribir la noticia con estilo humano, SEO y formato.
    """
    if not GROQ_API_KEY:
        print("   ⚠️ GROQ_API_KEY no configurada. Usando traducción simple.")
        return None

    user_prompt = f"""Fuente: {fuente_nombre}
Título original: {titulo}
Descripción original: {descripcion}

{'' if not articulo_completo else f'''
**CONTENIDO COMPLETO DEL ARTÍCULO (extraído):**
{articulo_completo['contenido'][:3500]}

**DATOS ESTRUCTURADOS EXTRAÍDOS:**
- Autor: {articulo_completo['autor']}
- Fecha: {articulo_completo['fecha']}
- Animes mencionados: {', '.join(articulo_completo['animes_mencionados']) if articulo_completo['animes_mencionados'] else 'No especificados'}
- Staff roles: {json.dumps(articulo_completo['staff'], indent=2) if articulo_completo['staff'] else 'No especificados'}
'''}

{'' if not sinopsis_data else f'''
**INFORMACIÓN ADICIONAL DEL ANIME RELACIONADO:**
Título: {sinopsis_data['titulo']}
Sinopsis: {sinopsis_data['sinopsis']}
Géneros: {sinopsis_data['generos']}
Puntaje en AniList: {sinopsis_data['puntaje']}/100
'''}

Reescribí esta noticia usando TODOS los datos disponibles. 
- Si hay una lista de animes o proyectos, menciónalos TODOS.
- Si hay nombres de personal (staff), inclúyelos con sus roles.
- Si hay cifras o fechas, inclúyelas con precisión.
- El artículo debe ser informativo, detallado y respetuoso.
- Nunca digas "no se han proporcionado detalles" o "falta de información".

Seguí la estructura de ejemplo y el tono profesional.
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

    extensions = ['extra', 'codehilite', 'toc', 'nl2br']

    try:
        html = markdown.markdown(texto_markdown, extensions=extensions)
        return html
    except Exception as e:
        print(f"   ⚠️ Error convirtiendo Markdown: {e}")
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
# FORMATO DEL POST CON MARKDOWN (ACTUALIZADO)
# ============================================
def formatear_contenido(texto_markdown, imagen, enlace, fuente):
    texto_html = convertir_markdown_a_html(texto_markdown)

    return f"""
<div style="text-align:center; margin-bottom:20px;">
<img src="{imagen}" alt="Anime" style="max-width:100%;border-radius:10px;"/>
</div>
<p><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
<p><strong>📰 Fuente Original:</strong> <a href="{enlace}" target="_blank" rel="noopener noreferrer">{fuente['categoria']}</a></p>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<div style="font-size:1.1rem;line-height:1.8;">
{texto_html}
</div>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<p style="text-align:center;font-size:0.9rem;">
<a href="{enlace}" target="_blank" rel="noopener noreferrer" style="color:#f43dce;text-decoration:none;font-weight:bold;">
📌 Fuente: Anime Actualidad Argentina - Te enteraste primero aquí
</a>
</p>
<p style="text-align:center;font-size:0.8rem;color:#9a9a9a;">
Anime Actualidad Argentina - Te enteraste primero aquí
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

                    # ---- SCRAPEAR ARTÍCULO COMPLETO ----
                    articulo_completo = None
                    if 'animenewsnetwork' in nombre_fuente and hasattr(entry, 'link'):
                        print(f"   🔍 Intentando scrapear artículo completo...")
                        articulo_completo = scrapear_articulo_ann(entry.link)
                        if articulo_completo:
                            print(f"   ✅ Artículo scrapeado: {len(articulo_completo['contenido'])} caracteres")
                            if articulo_completo['animes_mencionados'] and not sinopsis_data:
                                for anime_candidato in articulo_completo['animes_mencionados']:
                                    sinopsis_temp = buscar_sinopsis_anilist(anime_candidato)
                                    if sinopsis_temp:
                                        sinopsis_data = sinopsis_temp
                                        print(f"   ✅ Sinopsis obtenida para: {sinopsis_data['titulo']}")
                                        break

                    # ---- REESCRITURA CON GROQ ----
                    texto_para_ia = articulo_completo['contenido'] if articulo_completo else descripcion[:2000]

                    texto_markdown = reescribir_con_groq(
                        entry.title,
                        texto_para_ia[:3000],
                        nombre_fuente,
                        sinopsis_data,
                        articulo_completo
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

                    # ---- EXTRAER IMAGEN ----
                    imagen = extraer_imagen(entry)
                    if articulo_completo and articulo_completo.get('imagen'):
                        imagen = articulo_completo['imagen']

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

                time.sleep(3)

        except Exception as e:
            print(f"❌ Error procesando {nombre_fuente}: {e}")

    print("\n" + "="*60)
    print(f"✅ Proceso completado. {total_publicadas} borradores creados.")
    print("="*60)

if __name__ == "__main__":
    main()
