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
# EXTRACCIÓN DE NOTICIAS
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
                    descripcion_original = entry.description if 'description' in entry else ""

                    titulo_final, descripcion_final = ia.reescribir(entry.title, descripcion_original[:800])

                    imagen = extraer_imagen(entry)
                    contenido = formatear_contenido(descripcion_final, imagen, enlace, config_fuente)

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
