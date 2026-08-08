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

# Acepta "True", "true", "1" como verdadero; cualquier otra cosa (o vacío) es falso
MODO_PRUEBA = os.environ.get("MODO_PRUEBA", "False").strip().lower() in ("true", "1", "si", "sí")

ARCHIVO_PROCESADOS = "procesados.json"
MAX_PROCESADOS_GUARDADOS = 500  # evita que el archivo crezca indefinidamente
MAX_NOTICIAS_POR_FEED = 5

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
# PROCESADOS (evitar duplicados)
# ============================================
def cargar_procesados():
    """Carga la lista de links ya procesados desde procesados.json"""
    if not os.path.exists(ARCHIVO_PROCESADOS):
        return set()
    try:
        with open(ARCHIVO_PROCESADOS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("links", []))
    except Exception as e:
        print(f"⚠️ No se pudo leer {ARCHIVO_PROCESADOS}, se empieza de cero: {e}")
        return set()

def guardar_procesados(links_procesados):
    """Guarda la lista de links procesados, recortando al máximo permitido"""
    lista = list(links_procesados)[-MAX_PROCESADOS_GUARDADOS:]
    try:
        with open(ARCHIVO_PROCESADOS, "w", encoding="utf-8") as f:
            json.dump({"links": lista, "actualizado": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ No se pudo guardar {ARCHIVO_PROCESADOS}: {e}")

# ============================================
# AUTENTICACIÓN BLOGGER (OAuth con refresh token)
# ============================================
def autenticar_blogger():
    """Autentica con OAuth 2.0 usando un refresh token (sin navegador, apto para CI)"""

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

    creds.refresh(Request())  # canjea el refresh token por un access token válido
    return build('blogger', 'v3', credentials=creds)

# ============================================
# EXTRACCIÓN DE CONTENIDO COMPLETO (NUEVO)
# ============================================
def extraer_contenido_completo(url):
    """
    Entra a la página del artículo y extrae:
    - Texto completo del artículo
    - Imagen destacada (og:image o primera imagen grande)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return None, None

        soup = BeautifulSoup(response.text, 'html.parser')

        # ---- 1. EXTRAER IMAGEN DESTACADA ----
        imagen = None
        # Buscar en meta tags (og:image)
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            imagen = og_image['content']
        else:
            # Buscar la primera imagen grande del artículo
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src')
                if src and ('jpg' in src or 'png' in src or 'jpeg' in src):
                    width = img.get('width')
                    height = img.get('height')
                    if (width and int(width) > 200) or (height and int(height) > 200):
                        if not src.startswith('http'):
                            src = 'https:' + src if src.startswith('//') else src
                        imagen = src
                        break

        # ---- 2. EXTRAER TEXTO COMPLETO ----
        texto = None
        # Buscar en selectores comunes de ANN
        contenido = soup.find('div', class_='meat')
        if not contenido:
            contenido = soup.find('div', id='content')
        if not contenido:
            contenido = soup.find('article')
        if not contenido:
            contenido = soup.find('body')

        if contenido:
            # Eliminar elementos no deseados
            for tag in contenido.find_all(['script', 'style', 'noscript', 'iframe', 'ins']):
                tag.decompose()
            texto = contenido.get_text(separator='\n', strip=True)
            # Limpiar exceso de espacios y saltos de línea
            texto = re.sub(r'\n\s*\n', '\n\n', texto)
            texto = re.sub(r'[ \t]+', ' ', texto)

        return texto, imagen
    except Exception as e:
        print(f"   ⚠️ Error extrayendo contenido completo: {e}")
        return None, None

# ============================================
# REESCRITURA CON IA (DeepSeek primero, Groq de respaldo)
# ============================================
class IARedactor:
    """
    Traduce y reescribe título + descripción en español, con tono periodístico
    y SEO-friendly, usando DeepSeek como proveedor principal y Groq como respaldo.
    Si ambos fallan, devuelve el texto original sin modificar.
    """

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
                print(f"   ⚠️ DeepSeek respondió {response.status_code}: {response.text[:200]}")
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
                print(f"   ⚠️ Groq respondió {response.status_code}: {response.text[:200]}")
                return None
        except Exception as e:
            print(f"   ⚠️ Error llamando a Groq: {e}")
            return None

    def _parsear_json(self, contenido):
        """Extrae el JSON de la respuesta de la IA, tolerando texto extra o bloques ```json"""
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
        """
        Intenta reescribir con DeepSeek; si falla, prueba con Groq;
        si ambos fallan, devuelve el texto original sin traducir.
        """
        resultado = self._llamar_deepseek(titulo, descripcion)
        if resultado:
            print("   🧠 Reescrito con DeepSeek")
            return resultado

        resultado = self._llamar_groq(titulo, descripcion)
        if resultado:
            print("   🧠 Reescrito con Groq (respaldo)")
            return resultado

        print("   ⚠️ IA no disponible, se usa el texto original sin traducir")
        titulo_recortado = titulo[:62] + "..." if len(titulo) > 65 else titulo
        return titulo_recortado, descripcion

# ============================================
# EXTRACCIÓN DE IMAGEN (RESPALDO)
# ============================================
def extraer_imagen(entry):
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
    return "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjHDh8uCDe6OcMJuYQ48ZoDxLDetLv4bCgAesT2hZZrbTlsSVM-vSy-OlGjDnV5W9AE1Y8dapE-ANqUfwyDO2qzqpZRdFQxcAGsOwnYUslcyDuVKI4_zvyi01pgwaQHVqauXTnccYtxd0XLCbq8asfwWCQeXWfrzCJ0xhPiNfSR7zqFbWzy28kxGA"

# ============================================
# FORMATEAR CONTENIDO DEL BORRADOR (CON TEXTO COMPLETO)
# ============================================
def formatear_contenido_borrador(texto_completo, imagen, enlace, fuente, titulo_original):
    """
    Crea el contenido HTML del borrador con el texto completo extraído.
    El Bot 2 se encargará de darle formato y estilo.
    """
    # Limpiar texto HTML
    texto_limpio = re.sub(r'<[^>]+>', ' ', texto_completo)
    texto_limpio = re.sub(r'\s+', ' ', texto_limpio).strip()
    
    return f"""
<h1>{titulo_original}</h1>
<p><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
<p><strong>📰 Fuente:</strong> <a href="{enlace}" target="_blank" rel="noopener noreferrer">{fuente['categoria']}</a></p>
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
# FUNCIÓN PRINCIPAL
# ============================================
def main():
    print("="*60)
    print("🤖 BOT 1 (RECOLECTOR) - ANIME ACTUALIDAD ARGENTINA")
    print("="*60)

    if MODO_PRUEBA:
        print("🧪 MODO_PRUEBA activo: no se van a crear borradores reales ni gastar cuota de IA innecesariamente")

    # Verificar Blog ID
    if not BLOGGER_BLOG_ID:
        print("❌ Error: BLOGGER_BLOG_ID no está configurado")
        return
    print(f"✅ Blog ID: {BLOGGER_BLOG_ID}")

    # Verificar credenciales OAuth (solo si no estamos en modo prueba)
    service = None
    if not MODO_PRUEBA:
        if not BLOGGER_REFRESH_TOKEN or not BLOGGER_CLIENT_ID or not BLOGGER_CLIENT_SECRET:
            print("❌ Error: faltan BLOGGER_REFRESH_TOKEN, BLOGGER_CLIENT_ID o BLOGGER_CLIENT_SECRET")
            return
        try:
            service = autenticar_blogger()
            print("✅ Autenticación con Blogger exitosa")
        except Exception as e:
            print(f"❌ Error de autenticación: {e}")
            return

    if not DEEPSEEK_API_KEY and not GROQ_API_KEY:
        print("⚠️ Advertencia: no hay DEEPSEEK_API_KEY ni GROQ_API_KEY configuradas, no se reescribirá con IA")

    ia = IARedactor()
    procesados = cargar_procesados()
    print(f"📚 {len(procesados)} noticias ya procesadas anteriormente")

    total_nuevas = 0

    for nombre_fuente, config_fuente in RSS_FEEDS.items():
        print(f"\n📰 Procesando: {nombre_fuente}")
        try:
            feed = feedparser.parse(config_fuente["url"])
            print(f"   📡 {len(feed.entries)} noticias encontradas en el feed")

            for entry in feed.entries[:MAX_NOTICIAS_POR_FEED]:
                enlace = entry.link

                if enlace in procesados:
                    print(f"   ⏭️  Ya procesada, se omite: {entry.title[:50]}...")
                    continue

                try:
                    print(f"\n   📝 Procesando: {entry.title[:60]}...")

                    # ---- 1. EXTRAER CONTENIDO COMPLETO ----
                    texto_completo, imagen_completa = extraer_contenido_completo(enlace)

                    if texto_completo:
                        print(f"   ✅ Texto completo extraído: {len(texto_completo)} caracteres")
                        descripcion_para_ia = texto_completo[:800]  # Para el resumen de IA
                    else:
                        print(f"   ⚠️ No se pudo extraer texto completo, usando summary del RSS")
                        descripcion_original = entry.description if 'description' in entry else ""
                        texto_completo = descripcion_original
                        descripcion_para_ia = descripcion_original[:800]

                    # ---- 2. REESCRIBIR TÍTULO Y DESCRIPCIÓN CON IA ----
                    titulo_final, descripcion_resumen = ia.reescribir(entry.title, descripcion_para_ia)

                    # ---- 3. EXTRAER IMAGEN ----
                    if imagen_completa:
                        imagen = imagen_completa
                        print(f"   ✅ Imagen extraída de la página")
                    else:
                        imagen = extraer_imagen(entry)
                        print(f"   ℹ️ Usando imagen del RSS")

                    # ---- 4. FORMATEAR CONTENIDO DEL BORRADOR ----
                    # El borrador guarda el texto completo, no el resumen de IA
                    contenido = formatear_contenido_borrador(
                        texto_completo,
                        imagen,
                        enlace,
                        config_fuente,
                        titulo_final  # Usar el título editado por IA
                    )

                    # ---- 5. CREAR BORRADOR ----
                    if MODO_PRUEBA:
                        print(f"   🧪 [SIMULADO] Se crearía borrador: {titulo_final[:60]}")
                    else:
                        post = {
                            'title': titulo_final,
                            'content': contenido,
                            'labels': config_fuente['etiquetas'],
                            'status': 'DRAFT'
                        }
                        service.posts().insert(blogId=BLOGGER_BLOG_ID, body=post).execute()
                        print(f"   ✅ Borrador creado: {titulo_final[:60]}")

                    procesados.add(enlace)
                    total_nuevas += 1

                except Exception as e:
                    print(f"   ❌ Error al procesar noticia: {e}")

                time.sleep(2)

        except Exception as e:
            print(f"❌ Error procesando {nombre_fuente}: {e}")

    guardar_procesados(procesados)

    print("\n" + "="*60)
    if MODO_PRUEBA:
        print(f"🧪 Proceso de PRUEBA completado. {total_nuevas} noticias nuevas detectadas (no se publicó nada).")
    else:
        print(f"✅ Proceso completado. {total_nuevas} borradores nuevos creados.")
    print("="*60)

if __name__ == "__main__":
    main()
