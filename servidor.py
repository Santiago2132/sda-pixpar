import os
import json
import time
import socket
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from spyne import Application, rpc, ServiceBase, Unicode, Integer, ComplexModel, Array, AnyXml, Boolean, Float
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication
from werkzeug.serving import make_server
import xml.etree.ElementTree as ET
from typing import Dict, Any

# Configuración del balanceador
IP_BALANCEADOR = "192.168.154.130"  # Cambiar por la IP real del balanceador
PUERTO_BALANCEADOR = 5000

app = Flask(__name__)
CORS(app)

class BalanceadorClient:
    """Cliente para comunicarse con el balanceador de cargas"""
    
    def __init__(self, ip_balanceador: str, puerto: int = 5000):
        self.base_url = f"http://{ip_balanceador}:{puerto}/api"
        self.timeout = 30
    
    def registrar_nodo(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Registra un nodo en el balanceador"""
        try:
            response = requests.post(
                f"{self.base_url}/nodos/registrar",
                json=data,
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error conectando con balanceador: {str(e)}"}
    
    def procesar_imagenes(self, xml_content: str, prioridad: int = 5, 
                         tipo: str = "procesamiento_batch", formato: str = "JPEG", 
                         calidad: int = 85) -> Dict[str, Any]:
        """Envía una tarea de procesamiento al balanceador"""
        try:
            params = {
                "prioridad": prioridad,
                "tipo": tipo,
                "formato": formato,
                "calidad": calidad
            }
            response = requests.post(
                f"{self.base_url}/procesar",
                data=xml_content,
                headers={"Content-Type": "application/xml"},
                params=params,
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error procesando: {str(e)}"}
    
    def obtener_resultado(self, task_id: str) -> Dict[str, Any]:
        """Obtiene el resultado de una tarea"""
        try:
            response = requests.get(
                f"{self.base_url}/resultado/{task_id}",
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "status": "success",
                    "resultado": response.text,
                    "tiempo_proceso": response.headers.get("X-Processing-Time"),
                    "nodo_procesado": response.headers.get("X-Processed-By")
                }
            else:
                return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error obteniendo resultado: {str(e)}"}
    
    def obtener_estadisticas(self) -> Dict[str, Any]:
        """Obtiene estadísticas del balanceador"""
        try:
            response = requests.get(
                f"{self.base_url}/estadisticas",
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error obteniendo estadísticas: {str(e)}"}
    
    def listar_nodos(self) -> Dict[str, Any]:
        """Lista todos los nodos registrados"""
        try:
            response = requests.get(
                f"{self.base_url}/nodos",
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error listando nodos: {str(e)}"}
    
    def health_check(self) -> Dict[str, Any]:
        """Verifica el estado del balanceador"""
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": f"Error en health check: {str(e)}"}

# Instancia del cliente del balanceador
balanceador_client = BalanceadorClient(IP_BALANCEADOR, PUERTO_BALANCEADOR)

# Modelos SOAP
class RegistroNodoRequest(ComplexModel):
    ip = Unicode
    puertos = Array(Integer)
    capacidad_maxima = Integer

class RegistroNodoResponse(ComplexModel):
    status = Unicode
    message = Unicode

class ProcesarImagenesRequest(ComplexModel):
    xml_content = Unicode
    prioridad = Integer
    tipo_servicio = Unicode
    formato_salida = Unicode
    calidad = Integer

class ProcesarImagenesResponse(ComplexModel):
    status = Unicode
    task_id = Unicode
    message = Unicode

class ResultadoResponse(ComplexModel):
    status = Unicode
    resultado = Unicode
    tiempo_proceso = Unicode
    nodo_procesado = Unicode
    message = Unicode

class EstadisticasResponse(ComplexModel):
    tareas_procesadas = Integer
    tareas_pendientes = Integer
    nodos_activos = Integer
    tiempo_promedio = Float
    status = Unicode

class NodoInfo(ComplexModel):
    activo = Boolean
    capacidad_disponible = Integer
    capacidad_maxima = Integer
    ultimo_heartbeat = Float

class HealthResponse(ComplexModel):
    status = Unicode
    service = Unicode
    timestamp = Float
    nodos_activos = Integer

# Servicio SOAP
class BalanceadorSOAPService(ServiceBase):
    
    @rpc(RegistroNodoRequest, _returns=RegistroNodoResponse)
    def registrar_nodo(ctx, request):
        """Registra un nodo en el balanceador"""
        data = {
            "ip": request.ip,
            "puertos": request.puertos,
            "capacidad_maxima": request.capacidad_maxima or 100000,
            "servicios": {
                "procesamiento_batch": "8001",
                "transformaciones_batch": "8002", 
                "conversion_unica": "8004"
            }
        }
        
        resultado = balanceador_client.registrar_nodo(data)
        
        return RegistroNodoResponse(
            status=resultado.get("status", "error"),
            message=resultado.get("message", "Error desconocido")
        )
    
    @rpc(ProcesarImagenesRequest, _returns=ProcesarImagenesResponse)
    def procesar_imagenes(ctx, request):
        """Procesa imágenes enviando al balanceador"""
        resultado = balanceador_client.procesar_imagenes(
            xml_content=request.xml_content,
            prioridad=request.prioridad or 5,
            tipo=request.tipo_servicio or "procesamiento_batch",
            formato=request.formato_salida or "JPEG",
            calidad=request.calidad or 85
        )
        
        return ProcesarImagenesResponse(
            status=resultado.get("status", "error"),
            task_id=resultado.get("task_id", ""),
            message=resultado.get("message", "Error desconocido")
        )
    
    @rpc(Unicode, _returns=ResultadoResponse)
    def obtener_resultado(ctx, task_id):
        """Obtiene el resultado de una tarea"""
        resultado = balanceador_client.obtener_resultado(task_id)
        
        return ResultadoResponse(
            status=resultado.get("status", "error"),
            resultado=resultado.get("resultado", ""),
            tiempo_proceso=resultado.get("tiempo_proceso", ""),
            nodo_procesado=resultado.get("nodo_procesado", ""),
            message=resultado.get("message", "")
        )
    
    @rpc(_returns=EstadisticasResponse)
    def obtener_estadisticas(ctx):
        """Obtiene estadísticas del balanceador"""
        resultado = balanceador_client.obtener_estadisticas()
        
        return EstadisticasResponse(
            tareas_procesadas=resultado.get("tareas_procesadas", 0),
            tareas_pendientes=resultado.get("tareas_pendientes", 0),
            nodos_activos=resultado.get("nodos_activos", 0),
            tiempo_promedio=resultado.get("tiempo_promedio", 0.0),
            status=resultado.get("status", "success") if "error" not in resultado else "error"
        )
    
    @rpc(_returns=Array(Unicode))
    def listar_nodos(ctx):
        """Lista todos los nodos registrados"""
        resultado = balanceador_client.listar_nodos()
        
        if isinstance(resultado, dict) and "error" not in resultado:
            return list(resultado.keys())
        else:
            return ["Error: " + resultado.get("message", "Error desconocido")]
    
    @rpc(_returns=HealthResponse)
    def health_check(ctx):
        """Verifica el estado del balanceador"""
        resultado = balanceador_client.health_check()
        
        return HealthResponse(
            status=resultado.get("status", "error"),
            service=resultado.get("service", "Balanceador no disponible"),
            timestamp=resultado.get("timestamp", time.time()),
            nodos_activos=resultado.get("nodos_activos", 0)
        )

# Configuración de la aplicación SOAP
soap_app = Application(
    [BalanceadorSOAPService],
    tns='http://servidor.procesamiento.imagenes/soap',
    in_protocol=Soap11(validator='lxml'),
    out_protocol=Soap11()
)

# WSGI application para SOAP
soap_wsgi_app = WsgiApplication(soap_app)

# Rutas REST adicionales del servidor
@app.route('/api/config/balanceador', methods=['GET', 'POST'])
def config_balanceador():
    """Configurar IP del balanceador"""
    global IP_BALANCEADOR, balanceador_client
    
    if request.method == 'POST':
        data = request.get_json()
        nueva_ip = data.get('ip_balanceador')
        if nueva_ip:
            IP_BALANCEADOR = nueva_ip
            balanceador_client = BalanceadorClient(IP_BALANCEADOR, PUERTO_BALANCEADOR)
            return jsonify({"status": "success", "message": f"IP balanceador actualizada: {nueva_ip}"})
        else:
            return jsonify({"status": "error", "message": "IP no proporcionada"}), 400
    else:
        return jsonify({"ip_balanceador": IP_BALANCEADOR, "puerto": PUERTO_BALANCEADOR})

@app.route('/api/servidor/estado', methods=['GET'])
def estado_servidor():
    """Estado del servidor de aplicación"""
    # Verificar conexión con balanceador
    health_balanceador = balanceador_client.health_check()
    
    return jsonify({
        "servidor": "Servidor de Aplicación - Procesamiento de Imágenes",
        "estado": "activo",
        "timestamp": time.time(),
        "balanceador": {
            "ip": IP_BALANCEADOR,
            "puerto": PUERTO_BALANCEADOR,
            "conectado": health_balanceador.get("status") == "healthy"
        },
        "servicios": {
            "soap": "/soap",
            "wsdl": "/soap?wsdl",
            "rest_config": "/api/config/balanceador"
        }
    })

@app.route('/soap', methods=['POST'])
def soap_service():
    """Endpoint para servicios SOAP"""
    return soap_wsgi_app

@app.route('/soap', methods=['GET'])
def wsdl():
    """Obtener WSDL del servicio SOAP"""
    if request.args.get('wsdl') is not None:
        return Response(
            soap_app.interface.get_interface_document('http://localhost:8080/soap'),
            mimetype='text/xml'
        )
    return "Servicio SOAP activo. Agregue ?wsdl para obtener la definición del servicio."

@app.route('/', methods=['GET'])
def index():
    """Página principal con información del servidor"""
    return jsonify({
        "message": "Servidor de Aplicación - Procesamiento de Imágenes",
        "version": "1.0.0",
        "servicios": {
            "soap": {
                "url": "/soap",
                "wsdl": "/soap?wsdl",
                "metodos": [
                    "registrar_nodo",
                    "procesar_imagenes", 
                    "obtener_resultado",
                    "obtener_estadisticas",
                    "listar_nodos",
                    "health_check"
                ]
            },
            "rest": {
                "config_balanceador": "/api/config/balanceador",
                "estado_servidor": "/api/servidor/estado"
            }
        },
        "balanceador": f"{IP_BALANCEADOR}:{PUERTO_BALANCEADOR}"
    })

def main():
    """Función principal"""
    print("🚀 Iniciando Servidor de Aplicación...")
    print("=" * 60)
    
    # Obtener IP local
    ip_local = socket.gethostbyname(socket.gethostname())
    puerto = 8080
    
    print("📡 Servicios SOAP disponibles:")
    print("  • registrar_nodo - Registrar nodos en el balanceador")
    print("  • procesar_imagenes - Procesar imágenes vía balanceador")
    print("  • obtener_resultado - Obtener resultado de tarea")
    print("  • obtener_estadisticas - Estadísticas del balanceador")
    print("  • listar_nodos - Listar nodos registrados")
    print("  • health_check - Verificar estado del balanceador")
    
    print(f"\n🌐 Endpoints disponibles:")
    print(f"  • SOAP Service: http://{ip_local}:{puerto}/soap")
    print(f"  • WSDL: http://{ip_local}:{puerto}/soap?wsdl")
    print(f"  • REST Config: http://{ip_local}:{puerto}/api/config/balanceador")
    print(f"  • Estado: http://{ip_local}:{puerto}/api/servidor/estado")
    
    print(f"\n⚙️  Configuración:")
    print(f"  • Balanceador: {IP_BALANCEADOR}:{PUERTO_BALANCEADOR}")
    print(f"  • Servidor: {ip_local}:{puerto}")
    
    print(f"\n⚡ Servidor ejecutándose... (Ctrl+C para detener)")
    
    try:
        server = make_server(ip_local, puerto, app)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo servidor...")
        print("✅ Servidor detenido")

if __name__ == "__main__":
    main()