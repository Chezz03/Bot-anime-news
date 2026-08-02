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
    
    # Verificar si ya existe un token guardado
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # Si no hay credenciales o son inválidas, pedir autorización
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Usar el archivo credentials.json que descargaste
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Guardar el token para la próxima vez
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    # Construir el servicio de Blogger
    service = build('blogger', 'v3', credentials=creds)
    return service

# ============================================
# TRADUCCIÓN Y SEO
# ============================================

class Traductor:
    def __init__(self):
        self.api_url = "https://translate.googleapis.com/translate_a/single"
    
    def traducir(self, texto, target="es"):
        if not texto or len(texto.strip()) < 3:
            return texto
        try:
            params = {
                "client": "gtx",
                "sl": "auto",
                "tl": target,
                "dt": "t",
                "q": texto[:5000]
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

class OptimizadorSEO:
    def __init__(self):
        self.palabras_clave = ["anime", "manga", "estreno", "noticias", "japón", "cultura otaku"]
    
    def optimizar_titulo(self, titulo):
        traductor = Traductor()
        titulo_trad = traductor.traducir(titulo)
        if len(titulo_trad) > 65:
            titulo_trad = titulo_trad[:62] + "..."
        return titulo_trad

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
    print("🤖 BOT DE AUTOMATIZACIÓN - ANIME ACTUALIDAD ARGENTINA")
    print("="*60)
    
    # Verificar que el Blog ID esté configurado
    if not BLOGGER_BLOG_ID:
        print("❌ Error: BLOGGER_BLOG_ID no está configurado")
        return
    
    print(f"✅ Blog ID: {BLOGGER_BLOG_ID}")
    
    # Verificar que exista el archivo credentials.json
    if not os.path.exists('credentials.json'):
        print("❌ Error: No se encontró el archivo credentials.json")
        print("📂 Contenido del directorio:")
        for file in os.listdir('.'):
            print(f"   - {file}")
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
                    titulo_trad = seo.optimizar_titulo(entry.title)
                    descripcion = entry.description if 'description' in entry else ""
                    descripcion_trad = traductor.traducir(descripcion[:500])
                    imagen = extraer_imagen(entry)
                    contenido = formatear_contenido(descripcion_trad, imagen, entry.link, config_fuente)
                    
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
