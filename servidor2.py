import os
import threading
import time
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import xml.etree.ElementTree as ET
import xmlrpc.client
import base64
from werkzeug.serving import make_server

# Configuración
BALANCEADOR_IP = "192.168.154.129"
BALANCEADOR_RPC_URL = f"http://{BALANCEADOR_IP}:8000"

app = Flask(__name__)
CORS(app, origins="*")

class GestorSOAP:
    def __init__(self):
        self.balanceador_client = None
        self.resultados_cache = {}
        
    def get_balanceador_client(self):
        if self.balanceador_client is None:
            try:
                self.balanceador_client = xmlrpc.client.ServerProxy(BALANCEADOR_RPC_URL)
                self.balanceador_client.ping()
                print(f"Conectado con balanceador RPC: {BALANCEADOR_RPC_URL}")
            except Exception as e:
                print(f"Error conectando con balanceador: {e}")
                self.balanceador_client = None
        return self.balanceador_client
    
    def procesar_imagenes_async(self, xml_content, prioridad, tipo_servicio, formato_salida, calidad):
        try:
            client = self.get_balanceador_client()
            if not client:
                return {"error": "No se puede conectar con el balanceador", "task_id": None}
            
            # Validar XML
            try:
                ET.fromstring(xml_content)
            except:
                return {"error": "XML malformado", "task_id": None}
            
            # Enviar tarea al balanceador via RPC
            task_id = client.procesar_tarea(xml_content, prioridad, tipo_servicio, formato_salida, calidad)
            
            if task_id:
                self.resultados_cache[task_id] = {"status": "procesando", "timestamp": time.time()}
                
                # Iniciar monitoreo asíncrono
                threading.Thread(target=self._monitorear_tarea, args=(task_id,), daemon=True).start()
                
                return {
                    "task_id": task_id,
                    "status": "accepted",
                    "message": "Tarea enviada al balanceador"
                }
            else:
                return {"error": "Error procesando tarea en balanceador", "task_id": None}
                
        except Exception as e:
            return {"error": f"Error del servidor SOAP: {str(e)}", "task_id": None}
    
    def obtener_resultado_tarea(self, task_id):
        try:
            client = self.get_balanceador_client()
            if not client:
                return {"status": "error", "error": "No se puede conectar con el balanceador"}
            
            resultado_json = client.obtener_resultado(task_id)
            
            if resultado_json:
                resultado = json.loads(resultado_json)
                self.resultados_cache[task_id] = {
                    "status": resultado.get("status", "unknown"),
                    "resultado": resultado,
                    "timestamp": time.time()
                }
                return resultado
            else:
                return {"status": "not_found", "message": "Tarea no encontrada"}
                
        except Exception as e:
            return {"status": "error", "error": f"Error consultando resultado: {str(e)}"}
    
    def obtener_estadisticas_sistema(self):
        try:
            client = self.get_balanceador_client()
            if not client:
                return {"error": "No se puede conectar con el balanceador"}
            
            stats_json = client.obtener_estadisticas()
            if stats_json:
                return json.loads(stats_json)
            else:
                return {"error": "No se pudieron obtener estadísticas"}
                
        except Exception as e:
            return {"error": f"Error obteniendo estadísticas: {str(e)}"}
    
    def _monitorear_tarea(self, task_id):
        max_intentos = 60
        intentos = 0
        
        while intentos < max_intentos:
            try:
                time.sleep(5)
                intentos += 1
                
                client = self.get_balanceador_client()
                if not client:
                    break
                
                resultado_json = client.obtener_resultado(task_id)
                if resultado_json:
                    resultado = json.loads(resultado_json)
                    status = resultado.get("status", "unknown")
                    
                    self.resultados_cache[task_id] = {
                        "status": status,
                        "resultado": resultado,
                        "timestamp": time.time()
                    }
                    
                    if status in ["completado", "error"]:
                        print(f"Tarea {task_id} finalizada: {status}")
                        break
                        
            except Exception as e:
                print(f"Error monitoreando tarea {task_id}: {e}")
                break

# Instancia global del gestor
gestor = GestorSOAP()

@app.route('/procesar_imagenes', methods=['POST'])
def procesar_imagenes():
    try:
        data = request.get_json()
        
        xml_content = data.get('xml_content', '')
        prioridad = data.get('prioridad', 3)
        tipo_servicio = data.get('tipo_servicio', 'procesamiento_batch')
        formato_salida = data.get('formato_salida', 'JPEG')
        calidad = data.get('calidad', 85)
        
        resultado = gestor.procesar_imagenes_async(
            xml_content, prioridad, tipo_servicio, formato_salida, calidad
        )
        
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/obtener_resultado/<task_id>', methods=['GET'])
def obtener_resultado(task_id):
    try:
        resultado = gestor.obtener_resultado_tarea(task_id)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/estadisticas', methods=['GET'])
def estadisticas():
    try:
        stats = gestor.obtener_estadisticas_sistema()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    try:
        client = xmlrpc.client.ServerProxy(BALANCEADOR_RPC_URL)
        client.ping()
        balanceador_status = "connected"
    except:
        balanceador_status = "disconnected"
    
    return jsonify({
        "status": "healthy",
        "service": "Servidor SOAP - Intermediario de Procesamiento de Imágenes",
        "timestamp": time.time(),
        "balanceador_status": balanceador_status,
        "balanceador_url": BALANCEADOR_RPC_URL
    })

def main():
    print("Iniciando Servidor SOAP Intermediario...")
    
    ip_local = "192.168.154.130"
    puerto = 5001
    
    print(f"Endpoints disponibles:")
    print(f"  POST /procesar_imagenes - Procesar imágenes")
    print(f"  GET /obtener_resultado/<task_id> - Obtener resultado")
    print(f"  GET /estadisticas - Ver estadísticas")
    print(f"  GET /health - Health check")
    
    print(f"\nURL base: http://{ip_local}:{puerto}")
    print(f"Balanceador: {BALANCEADOR_RPC_URL}")
    print("Servidor ejecutándose...")
    
    try:
        server = make_server(ip_local, puerto, app)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")

if __name__ == "__main__":
    main()