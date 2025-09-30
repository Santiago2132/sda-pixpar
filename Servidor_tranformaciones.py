import os
import json
import uuid
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import socket
import subprocess
import re

def obtener_ip_real():
    """Obtiene la IP real de la máquina en la red local."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except:
        pass
    return "127.0.0.1"

app = Flask(__name__)
CORS(app)

# Configuración del balanceador
BALANCEADOR_URL = "http://192.168.154.129:5000"

@app.route('/transformar', methods=['POST'])
def transformar_imagen():
    """Proxy para transformaciones - envía al balanceador"""
    try:
        # Obtener XML del cuerpo
        if request.content_type in ['application/xml', 'text/xml']:
            xml_content = request.data.decode('utf-8')
        else:
            xml_content = request.get_data(as_text=True)
        
        if not xml_content:
            return jsonify({"error": "No se recibió contenido XML"}), 400
        
        # Obtener configuración del header
        config_header = request.headers.get('X-Transformaciones')
        if not config_header:
            return jsonify({"error": "Header X-Transformaciones requerido"}), 400
        
        try:
            config = json.loads(config_header)
        except:
            return jsonify({"error": "X-Transformaciones debe ser JSON válido"}), 400
        
        # Convertir configuración a XML con transformaciones embebidas
        xml_con_transformaciones = agregar_transformaciones_a_xml(xml_content, config)
        
        # Determinar tipo de servicio
        tipo_servicio = "transformaciones_batch"
        prioridad = int(request.args.get('prioridad', 5))
        
        # Enviar al balanceador
        response = requests.post(
            f"{BALANCEADOR_URL}/api/procesar",
            data=xml_con_transformaciones,
            headers={'Content-Type': 'application/xml'},
            params={
                'tipo': tipo_servicio,
                'prioridad': prioridad,
                'formato': config.get('formato_salida', 'JPEG'),
                'calidad': config.get('calidad', 85)
            }
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            return jsonify({"error": "Error en balanceador", "details": response.text}), response.status_code
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/resultado/<task_id>', methods=['GET'])
def obtener_resultado(task_id):
    """Proxy para obtener resultado del balanceador"""
    try:
        response = requests.get(f"{BALANCEADOR_URL}/api/resultado/{task_id}")
        
        if response.status_code == 200:
            # Si es XML, pasarlo tal como viene
            if response.headers.get('content-type') == 'application/xml':
                return Response(
                    response.content,
                    mimetype='application/xml',
                    headers=dict(response.headers)
                )
            else:
                return response.json()
        else:
            return jsonify({"error": "Resultado no encontrado"}), response.status_code
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/estado/<task_id>', methods=['GET'])
def consultar_estado(task_id):
    """Consulta estado mediante estadísticas del balanceador"""
    try:
        # Intentar obtener resultado primero
        resultado_response = requests.get(f"{BALANCEADOR_URL}/api/resultado/{task_id}")
        
        if resultado_response.status_code == 200:
            return jsonify({
                "task_id": task_id,
                "status": "completado"
            })
        elif resultado_response.status_code == 404:
            # Verificar estadísticas para ver si está procesando
            stats_response = requests.get(f"{BALANCEADOR_URL}/api/estadisticas")
            if stats_response.status_code == 200:
                stats = stats_response.json()
                if stats.get("tareas_pendientes", 0) > 0:
                    return jsonify({
                        "task_id": task_id,
                        "status": "procesando"
                    })
                else:
                    return jsonify({
                        "task_id": task_id,
                        "status": "not_found"
                    }), 404
        else:
            return jsonify({
                "task_id": task_id,
                "status": "error",
                "error": "Error consultando balanceador"
            }), 500
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check - verifica conexión con balanceador"""
    try:
        response = requests.get(f"{BALANCEADOR_URL}/api/health", timeout=5)
        if response.status_code == 200:
            return jsonify({
                "status": "healthy",
                "service": "Proxy Servidor de Transformaciones",
                "balanceador_status": "connected",
                "balanceador_url": BALANCEADOR_URL
            })
        else:
            return jsonify({
                "status": "degraded",
                "service": "Proxy Servidor de Transformaciones", 
                "balanceador_status": "error"
            }), 503
    except:
        return jsonify({
            "status": "unhealthy",
            "service": "Proxy Servidor de Transformaciones",
            "balanceador_status": "disconnected",
            "error": "No se puede conectar al balanceador"
        }), 503

def agregar_transformaciones_a_xml(xml_content, config):
    """Agrega transformaciones como atributos al XML"""
    import xml.etree.ElementTree as ET
    
    try:
        root = ET.fromstring(xml_content)
        
        # Procesar cada imagen
        for imagen in root.findall('imagen'):
            # Crear string de transformaciones
            transformaciones = []
            for trans in config.get('transformaciones', []):
                tipo = trans['tipo']
                params = trans.get('parametros', {})
                
                if tipo == "redimensionar":
                    transformaciones.append(f"redimensionar_{params.get('ancho', 200)}x{params.get('alto', 200)}")
                elif tipo == "rotar":
                    transformaciones.append(f"rotar_{params.get('angulo', 45)}")
                elif tipo == "recortar":
                    x1, y1, x2, y2 = params.get('x1', 0), params.get('y1', 0), params.get('x2', 100), params.get('y2', 100)
                    transformaciones.append(f"recortar_{x1}_{y1}_{x2}_{y2}")
                elif tipo == "reflejar":
                    transformaciones.append(f"reflejar_{params.get('direccion', 'horizontal')}")
                elif tipo == "desenfocar":
                    transformaciones.append(f"desenfocar_{params.get('radio', 2)}")
                elif tipo == "perfilar":
                    transformaciones.append(f"perfilar_{params.get('factor', 2.0)}")
                elif tipo == "brillo_contraste":
                    b = params.get('brillo', 1.0)
                    c = params.get('contraste', 1.0)
                    transformaciones.append(f"brillo_{b}_contraste_{c}")
                elif tipo == "texto":
                    texto = params.get('texto', 'Marca')
                    x, y = params.get('x', 10), params.get('y', 10)
                    r, g, b = params.get('color_r', 255), params.get('color_g', 255), params.get('color_b', 255)
                    transformaciones.append(f"texto_{texto}_{x}_{y}_{r}_{g}_{b}")
                elif tipo == "escala_grises":
                    transformaciones.append("escala_grises")
            
            # Agregar transformaciones como atributo
            if transformaciones:
                imagen.set('transformaciones', ', '.join(transformaciones))
            
            # Agregar formato si no existe
            if not imagen.get('formato'):
                imagen.set('formato', config.get('formato_salida', 'JPEG'))
        
        return ET.tostring(root, encoding='unicode')
    
    except Exception as e:
        print(f"Error procesando XML: {e}")
        return xml_content

def main():
    """Función principal"""
    print("Iniciando Servidor Proxy de Transformaciones...")
    print("=" * 50)
    
    ip_local = obtener_ip_real()
    puerto = 8081
    
    print(f"IP del servidor: {ip_local}:{puerto}")
    print(f"Balanceador configurado: {BALANCEADOR_URL}")
    print("\nEndpoints disponibles:")
    print("  • POST /transformar - Enviar transformaciones al balanceador")
    print("  • GET /resultado/<task_id> - Obtener resultado del balanceador")  
    print("  • GET /estado/<task_id> - Consultar estado de tarea")
    print("  • GET /health - Health check")
    print("\nServidor listo...")
    
    try:
        app.run(host=ip_local, port=puerto, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")

if __name__ == "__main__":
    main()