import feedparser
import requests
import re
import json
import os
from datetime import datetime
import time
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# ============================================
# CONFIGURACIÓN
# ============================================
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
BLOGGER_CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "")
BLOGGER_CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "")
BLOGGER_REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

MODO_PRUEBA = os.environ.get("MODO_PRUEBA", "False").strip().lower() in ("true", "1", "si", "sí")

ARCHIVO_PROCESADOS = "procesados.json"
MAX_PROCESADOS_GUARDADOS = 500
MAX_NOTICIAS_POR_FEED = 4  # Reducido para no saturar la API

# ============================================
# FUENTES DE NOTICIAS (RSS + Web Scraping)
# ============================================
def extraer_noticias_crunchyroll():
    """Extrae las noticias de la página de Crunchyroll News."""
    url = "https://www.crunchyroll.com/es/news"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"   ❌ Error al obtener Crunchyroll: {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        noticias = []

        # Buscar elementos de noticias
        for article in soup.find_all(['article', 'div'], class_=re.compile(r'news|article|item')):
            titulo_elem = article.find(['h2', 'h3', 'a'])
            if titulo_elem:
                titulo = titulo_elem.get_text(strip=True)
                enlace = titulo_elem.get('href')
                if enlace and not enlace.startswith('http'):
                    enlace = "https://www.crunchyroll.com" + enlace
                
                desc_elem = article.find('p')
                descripcion = desc_elem.get_text(strip=True) if desc_elem else ""
                
                noticias.append({
                    'titulo': titulo,
                    'enlace': enlace,
                    'descripcion': descripcion,
                    'fuente': 'Crunchyroll'
                })
        
        print(f"   📥 Extraídas {len(noticias)} noticias de Crunchyroll")
        return noticias[:MAX_NOTICIAS_POR_FEED]
    except Exception as e:
        print(f"   ❌ Error al extraer Crunchyroll: {e}")
        return []

def extraer_noticias_myanimelist():
    """Extrae las noticias de la página de MyAnimeList News."""
    url = "https://myanimelist.net/news"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"   ❌ Error al obtener MyAnimeList: {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        noticias = []

        for item in soup.find_all('div', class_='news-unit'):
            titulo_elem = item.find('a', class_='title')
            if titulo_elem:
                titulo = titulo_elem.get_text(strip=True)
                enlace = titulo_elem.get('href')
                if enlace and not enlace.startswith('http'):
                    enlace = "https://myanimelist.net" + enlace
                
                desc_elem = item.find('div', class_='text')
                descripcion = desc_elem.get_text(strip=True) if desc_elem else ""
                
                noticias.append({
                    'titulo': titulo,
                    'enlace': enlace,
                    'descripcion': descripcion,
                    'fuente': 'MyAnimeList'
                })
        
        print(f"   📥 Extraídas {len(noticias)} noticias de MyAnimeList")
        return noticias[:MAX_NOTICIAS_POR_FEED]
    except Exception as e:
        print(f"   ❌ Error al extraer MyAnimeList: {e}")
        return []

# ============================================
# FUENTES CONFIGURADAS
# ============================================
FUENTES = {
    "animenewsnetwork": {
        "tipo": "rss",
        "url": "https://www.animenewsnetwork.com/newsfeed/rss.xml",
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Internacional", "Noticias"],
        "filtrar_resenas": True
    },
    "myanimelist": {
        "tipo": "web",
        "funcion": extraer_noticias_myanimelist,
        "categoria": "Noticias",
        "etiquetas": ["Anime", "MyAnimeList"],
        "filtrar_resenas": False
    },
    "crunchyroll": {
        "tipo": "web",
        "funcion": extraer_noticias_crunchyroll,
        "categoria": "Noticias",
        "etiquetas": ["Anime", "Estrenos", "Crunchyroll"],
        "filtrar_resenas": False
    }
}

SCOPES = ['https://www.googleapis.com/auth/blogger']

# ============================================
# PROCESADOS (evitar duplicados)
# ============================================
def cargar_procesados():
    if not os.path.exists(ARCHIVO_PROCESADOS):
        return set()
    try:
        with open(ARCHIVO_PROCESADOS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("links", []))
    except Exception as e:
        print(f"⚠️ No se pudo leer {ARCHIVO_PROCESADOS}: {e}")
        return set()

def guardar_procesados(links_procesados):
    lista = list(links_procesados)[-MAX_PROCESADOS_GUARDADOS:]
    try:
        with open(ARCHIVO_PROCESADOS, "w", encoding="utf-8") as f:
            json.dump({"links": lista, "actualizado": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ No se pudo guardar {ARCHIVO_PROCESADOS}: {e}")

# ============================================
# AUTENTICACIÓN BLOGGER
# ============================================
def autenticar_blogger():
    if not BLOGGER_REFRESH_TOKEN:
        raise ValueError("BLOGGER_REFRESH_TOKEN no está configurado")
    if not BLOGGER_CLIENT_ID:
        raise ValueError("BLOGGER_CLIENT_ID no está configurado")
    if not BLOGGER_CLIENT_SECRET:
        raise ValueError("BLOGGER_CLIENT_SECRET no está configurado")

    creds = Credentials(
        token=None,
        refresh_token=BLOGGER_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=BLOGGER_CLIENT_ID,
        client_secret=BLOGGER_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build('blogger', 'v3', credentials=creds)

# ============================================
# EXTRACCIÓN DE CONTENIDO COMPLETO
# ============================================
def extraer_contenido_completo(url):
    """
    Entra a la página del artículo y extrae:
    - Texto completo del artículo
    - Imagen destacada (og:image, twitter:image, o primera imagen grande)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return None, None

        soup = BeautifulSoup(response.text, 'lxml')

        # ---- IMAGEN ----
        imagen = None
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            imagen = og_image['content']
            print(f"   🖼️ Imagen encontrada en og:image")
        else:
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                imagen = twitter_image['content']
                print(f"   🖼️ Imagen encontrada en twitter:image")
            else:
                contenido = soup.find('div', class_='meat') or soup.find('div', id='content') or soup.find('article')
                if contenido:
                    for img in contenido.find_all('img'):
                        src = img.get('src') or img.get('data-src')
                        if src and ('jpg' in src.lower() or 'png' in src.lower() or 'jpeg' in src.lower()):
                            if not src.startswith('http'):
                                src = 'https:' + src if src.startswith('//') else src
                            width = img.get('width')
                            height = img.get('height')
                            if (width and int(width) > 100) or (height and int(height) > 100):
                                imagen = src
                                print(f"   🖼️ Imagen encontrada en el contenido")
                                break

        # ---- TEXTO ----
        texto = None
        contenido = soup.find('div', class_='meat')
        if not contenido:
            contenido = soup.find('div', id='content')
        if not contenido:
            contenido = soup.find('article')
        if not contenido:
            contenido = soup.find('body')

        if contenido:
            for tag in contenido.find_all(['script', 'style', 'noscript', 'iframe', 'ins']):
                tag.decompose()
            texto = contenido.get_text(separator='\n', strip=True)
            texto = re.sub(r'\n\s*\n', '\n\n', texto)
            texto = re.sub(r'[ \t]+', ' ', texto)

        return texto, imagen
    except Exception as e:
        print(f"   ⚠️ Error extrayendo contenido completo: {e}")
        return None, None

# ============================================
# REESCRITURA CON IA
# ============================================
class IARedactor:
    PROMPT_SISTEMA = (
        "Sos un redactor de noticias de anime en español (de Argentina). "
        "Tu tarea es traducir y reescribir brevemente el título y la descripción "
        "que te paso, manteniendo los datos concretos (nombres, fechas, estudios), "
        "con un tono periodístico ágil y natural, apto para un blog de noticias. "
        "Respondé ÚNICAMENTE con un JSON válido de la forma: "
        '{"titulo": "...", "descripcion": "..."}. '
        "El título no debe superar los 65 caracteres. "
        "La descripción debe tener entre 2 y 4 oraciones."
    )

    def __init__(self):
        self.deepseek_url = "https://api.deepseek.com/chat/completions"
        self.groq_url = "https://api.groq.com/openai/v1/chat/completions"

    def _prompt_usuario(self, titulo, descripcion):
        return f"Título original: {titulo}\n\nDescripción original: {descripcion}"

    def _llamar_deepseek(self, titulo, descripcion):
        if not DEEPSEEK_API_KEY:
            return None
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": self.PROMPT_SISTEMA},
                    {"role": "user", "content": self._prompt_usuario(titulo, descripcion)},
                ],
                "temperature": 0.5,
                "max_tokens": 500,
            }
            response = requests.post(self.deepseek_url, headers=headers, json=body, timeout=30)
            if response.status_code == 200:
                data = response.json()
                contenido = data["choices"][0]["message"]["content"]
                return self._parsear_json(contenido)
            else:
                print(f"   ⚠️ DeepSeek respondió {response.status_code}")
                return None
        except Exception as e:
            print(f"   ⚠️ Error llamando a DeepSeek: {e}")
            return None

    def _llamar_groq(self, titulo, descripcion):
        if not GROQ_API_KEY:
            return None
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": self.PROMPT_SISTEMA},
                    {"role": "user", "content": self._prompt_usuario(titulo, descripcion)},
                ],
                "temperature": 0.5,
                "max_tokens": 500,
            }
            response = requests.post(self.groq_url, headers=headers, json=body, timeout=30)
            if response.status_code == 200:
                data = response.json()
                contenido = data["choices"][0]["message"]["content"]
                return self._parsear_json(contenido)
            else:
                print(f"   ⚠️ Groq respondió {response.status_code}")
                return None
        except Exception as e:
            print(f"   ⚠️ Error llamando a Groq: {e}")
            return None

    def _parsear_json(self, contenido):
        try:
            contenido = contenido.strip()
            contenido = re.sub(r'^```json\s*|\s*```$', '', contenido, flags=re.MULTILINE).strip()
            data = json.loads(contenido)
            if "titulo" in data and "descripcion" in data:
                return data["titulo"].strip(), data["descripcion"].strip()
        except Exception as e:
            print(f"   ⚠️ No se pudo interpretar la respuesta de la IA: {e}")
        return None

    def reescribir(self, titulo, descripcion):
        resultado = self._llamar_deepseek(titulo, descripcion)
        if resultado:
            print("   🧠 Reescrito con DeepSeek")
            return resultado

        resultado = self._llamar_groq(titulo, descripcion)
        if resultado:
            print("   🧠 Reescrito con Groq (respaldo)")
            return resultado

        print("   ⚠️ IA no disponible, se usa el texto original")
        titulo_recortado = titulo[:62] + "..." if len(titulo) > 65 else titulo
        return titulo_recortado, descripcion

# ============================================
# EXTRACCIÓN DE IMAGEN DESDE RSS (RESPALDO)
# ============================================
def extraer_imagen_desde_rss(entry):
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
            return img_match[1]
    return None

# ============================================
# FORMATEAR CONTENIDO DEL BORRADOR
# ============================================
def formatear_contenido_borrador(texto_completo, imagen, enlace, fuente, titulo_original):
    texto_limpio = re.sub(r'<[^>]+>', ' ', texto_completo)
    texto_limpio = re.sub(r'\s+', ' ', texto_limpio).strip()
    
    return f"""
<h1>{titulo_original}</h1>
<p><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
<p><strong>📰 Fuente:</strong> <a href="{enlace}" target="_blank" rel="noopener noreferrer">{fuente}</a></p>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<div style="font-size:1.1rem;line-height:1.8;">
{texto_limpio.replace(chr(10), '<br>')}
</div>
<hr style="border-color:#f43dce;border-width:1px;margin:20px 0;">
<p style="text-align:center;font-size:0.9rem;">
<a href="{enlace}" target="_blank" rel="noopener noreferrer" style="color:#f43dce;text-decoration:none;font-weight:bold;">
🔗 Leer noticia completa →
</a>
</p>
<p style="text-align:center;font-size:0.8rem;color:#9a9a9a;">
<em>Borrador generado por Bot Recolector - Esperando edición del Bot Editor</em>
</p>
"""

# ============================================
# CREAR BORRADOR EN BLOGGER
# ============================================
def crear_borrador_blogger(service, titulo, contenido, etiquetas):
    """Crea un borrador en Blogger usando el servicio autenticado."""
    post = {
        'title': titulo,
        'content': contenido,
        'labels': etiquetas,
        'status': 'DRAFT'
    }
    return service.posts().insert(blogId=BLOGGER_BLOG_ID, body=post).execute()

# ============================================
# FUNCIÓN PRINCIPAL
# ============================================
def main():
    print("="*60)
    print("🤖 BOT 1 (RECOLECTOR) - ANIME ACTUALIDAD ARGENTINA")
    print("="*60)

    if MODO_PRUEBA:
        print("🧪 MODO_PRUEBA activo")
    else:
        print("🔄 Modo producción: se crearán borradores reales")

    if not BLOGGER_BLOG_ID:
        print("❌ Error: BLOGGER_BLOG_ID no está configurado")
        return
    print(f"✅ Blog ID: {BLOGGER_BLOG_ID}")

    service = None
    if not MODO_PRUEBA:
        if not BLOGGER_REFRESH_TOKEN or not BLOGGER_CLIENT_ID or not BLOGGER_CLIENT_SECRET:
            print("❌ Error: faltan credenciales OAuth")
            return
        try:
            service = autenticar_blogger()
            print("✅ Autenticación con Blogger exitosa")
        except Exception as e:
            print(f"❌ Error de autenticación: {e}")
            return

    ia = IARedactor()
    procesados = cargar_procesados()
    print(f"📚 {len(procesados)} noticias ya procesadas anteriormente")

    total_nuevas = 0

    for nombre_fuente, config_fuente in FUENTES.items():
        print(f"\n📰 Procesando: {nombre_fuente}")
        
        entradas = []
        if config_fuente["tipo"] == "rss":
            try:
                feed = feedparser.parse(config_fuente["url"])
                entradas = feed.entries[:MAX_NOTICIAS_POR_FEED]
                print(f"   📡 {len(entradas)} noticias encontradas en RSS")
            except Exception as e:
                print(f"   ❌ Error al parsear RSS: {e}")
                continue
        else:
            entradas_raw = config_fuente["funcion"]()
            entradas = [{
                'title': e['titulo'],
                'link': e['enlace'],
                'description': e['descripcion'],
                'fuente': e['fuente']
            } for e in entradas_raw]
            print(f"   📡 {len(entradas)} noticias encontradas en web")

        for entry in entradas:
            enlace = entry.get('link', '')
            if not enlace:
                print(f"   ⚠️ Noticia sin enlace: {entry.get('title', 'Sin título')[:50]}...")
                continue

            if enlace in procesados:
                print(f"   ⏭️ Ya procesada: {entry.get('title', 'Sin título')[:50]}...")
                continue

            # Filtrar reseñas solo para ANN
            if config_fuente.get("filtrar_resenas", False):
                etiquetas = entry.get('tags', [])
                es_resena = any(tag.get('term', '').lower() in ['review', 'feature', 'interview', 'column'] for tag in etiquetas)
                if es_resena:
                    print(f"   ⏭️ Saltando reseña/feature: {entry.get('title', 'Sin título')[:50]}...")
                    continue

            print(f"\n   📝 Procesando: {entry.get('title', 'Sin título')[:60]}...")

            try:
                # Extraer contenido completo
                texto_completo, imagen_completa = extraer_contenido_completo(enlace)

                if texto_completo:
                    descripcion_para_ia = texto_completo[:800]
                    print(f"   ✅ Texto completo extraído: {len(texto_completo)} caracteres")
                else:
                    descripcion_original = entry.get('description', '')
                    texto_completo = descripcion_original
                    descripcion_para_ia = descripcion_original[:800]
                    print(f"   ⚠️ Usando descripción del RSS ({len(texto_completo)} caracteres)")

                # Reescritura con IA
                titulo_original = entry.get('title', 'Sin título')
                titulo_final, _ = ia.reescribir(titulo_original, descripcion_para_ia)

                # Imagen
                if imagen_completa:
                    imagen = imagen_completa
                    print(f"   ✅ Imagen extraída de la página")
                else:
                    imagen = extraer_imagen_desde_rss(entry)
                    if imagen:
                        print(f"   ℹ️ Imagen extraída del RSS")
                    else:
                        imagen = "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjHDh8uCDe6OcMJuYQ48ZoDxLDetLv4bCgAesT2hZZrbTlsSVM-vSy-OlGjDnV5W9AE1Y8dapE-ANqUfwyDO2qzqpZRdFQxcAGsOwnYUslcyDuVKI4_zvyi01pgwaQHVqauXTnccYtxd0XLCbq8asfwWCQeXWfrzCJ0xhPiNfSR7zqFbWzy28kxGA"
                        print(f"   ℹ️ Usando imagen por defecto")

                # Formatear contenido del borrador
                fuente_nombre = entry.get('fuente', nombre_fuente)
                contenido = formatear_contenido_borrador(
                    texto_completo,
                    imagen,
                    enlace,
                    fuente_nombre,
                    titulo_final
                )

                # Crear borrador
                if MODO_PRUEBA:
                    print(f"   🧪 [SIMULADO] Borrador: {titulo_final[:60]}")
                else:
                    resultado = crear_borrador_blogger(
                        service,
                        titulo_final,
                        contenido,
                        config_fuente['etiquetas']
                    )
                    print(f"   ✅ Borrador creado: {titulo_final[:60]}")

                procesados.add(enlace)
                guardar_procesados(procesados)
                total_nuevas += 1

                time.sleep(5)  # Pausa para no saturar la API

            except Exception as e:
                print(f"   ❌ Error al procesar noticia: {e}")

    print("\n" + "="*60)
    if MODO_PRUEBA:
        print(f"🧪 Prueba completada. {total_nuevas} noticias nuevas detectadas.")
    else:
        print(f"✅ Proceso completado. {total_nuevas} borradores nuevos creados.")
    print("="*60)

if __name__ == "__main__":
    main()
