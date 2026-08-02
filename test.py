import os
import json

print("="*50)
print("🧪 TEST DE DIAGNÓSTICO - BOT ANIME")
print("="*50)

# 1. Verificar Blog ID
blog_id = os.environ.get('BLOGGER_BLOG_ID', '')
if blog_id:
    print(f"✅ Blog ID: {blog_id}")
else:
    print("❌ BLOGGER_BLOG_ID no está configurado")

# 2. Verificar credentials.json
print("\n📂 Verificando archivos...")
if os.path.exists('credentials.json'):
    print("✅ credentials.json encontrado")
    try:
        with open('credentials.json', 'r') as f:
            data = json.load(f)
            print(f"✅ Cliente ID: {data['installed']['client_id'][:30]}...")
            print(f"✅ Project ID: {data['installed']['project_id']}")
    except Exception as e:
        print(f"❌ Error al leer credentials.json: {e}")
else:
    print("❌ credentials.json NO encontrado")
    print("📂 Contenido del directorio:")
    for file in os.listdir('.'):
        print(f"   - {file}")

# 3. Verificar dependencias
print("\n📦 Verificando dependencias...")
try:
    import feedparser
    print("✅ feedparser instalado")
except ImportError:
    print("❌ feedparser NO instalado")

try:
    import requests
    print("✅ requests instalado")
except ImportError:
    print("❌ requests NO instalado")

try:
    from google.oauth2.credentials import Credentials
    print("✅ google-auth instalado")
except ImportError:
    print("❌ google-auth NO instalado")

print("\n" + "="*50)
print("✅ Test completado")
print("="*50)
