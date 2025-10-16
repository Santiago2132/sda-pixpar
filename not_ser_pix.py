# server2.py
import os
import time
from flask import Flask, request, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type"], methods=["GET", "POST", "OPTIONS"])

@app.route('/notificacion', methods=['POST', 'OPTIONS'])
def recibir_notificacion():
    if request.method == 'OPTIONS':
        response = Response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
        return response
    
    try:
        data = request.json
        evento = data.get('evento', 'Evento desconocido')
        hora = data.get('hora', time.strftime("%Y-%m-%d %H:%M:%S"))
        print(f"Notificación recibida: {evento} a las {hora}")
        return {"status": "recibido"}, 200
    except Exception as e:
        print(f"Error recibiendo notificación: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/health', methods=['GET'])
def health_check():
    return {
        "status": "healthy",
        "service": "Servidor de Notificaciones",
        "timestamp": time.time()
    }

if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5002))
    print("Servidor de Notificaciones iniciando...")
    print(f"Escuchando en 0.0.0.0:{puerto}")
    app.run(host='0.0.0.0', port=puerto, debug=False, threaded=True)