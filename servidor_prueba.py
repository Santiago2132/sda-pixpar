import os
import json
import time
import socket
import requests
import asyncio
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from werkzeug.serving import make_server
import xml.etree.ElementTree as ET
from typing import Dict, Any
import uuid

# Configuración del balanceador
IP_BALANCEADOR = "192.168.154.129"  # Cambiar por la IP real del balanceador
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
    
    async def procesar_imagenes_con_polling(self, xml_content: str, prioridad: int = 5, 
                                           tipo: str = "procesamiento_batch", formato: str = "JPEG", 
                                           calidad: int = 85, poll_interval: float = 5.0, 
                                           max_attempts: int = 60) -> Dict[str, Any]:
        """Procesa imágenes con polling automático hasta obtener el resultado"""
        # Enviar tarea inicial
        result = self.procesar_imagenes(xml_content, prioridad, tipo, formato, calidad)
        if result.get("status") != "success" or "task_id" not in result:
            return result
        
        task_id = result["task_id"]
        attempts = 0
        
        while attempts < max_attempts:
            await asyncio.sleep(poll_interval)
            resultado = self.obtener_resultado(task_id)
            if resultado.get("status") == "success":
                return resultado
            elif resultado.get("status") == "error":
                return resultado
            attempts += 1
        
        return {"status": "error", "message": f"Tarea {task_id} no completada tras {max_attempts} intentos"}
    
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

def crear_soap_response(data: Dict[str, Any], operation: str) -> str:
    """Crea una respuesta SOAP XML"""
    response_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:{operation}Response>'''
    
    for key, value in data.items():
        if isinstance(value, dict):
            response_xml += f'<tns:{key}>'
            for subkey, subvalue in value.items():
                response_xml += f'<tns:{subkey}>{subvalue}</tns:{subkey}>'
            response_xml += f'</tns:{key}>'
        else:
            response_xml += f'<tns:{key}>{value}</tns:{key}>'
    
    response_xml += f'''
        </tns:{operation}Response>
    </soap:Body>
</soap:Envelope>'''
    
    return response_xml

def extraer_soap_request(xml_content: str) -> Dict[str, Any]:
    """Extrae datos de una petición SOAP"""
    try:
        root = ET.fromstring(xml_content)
        
        # Buscar el body
        body = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Body')
        if body is None:
            return {}
        
        # Extraer todos los elementos del body
        data = {}
        for element in body:
            operation = element.tag.split('}')[-1] if '}' in element.tag else element.tag
            data['operation'] = operation
            
            for child in element:
                key = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                data[key] = child.text
        
        return data
    except:
        return {}

# Servicio SOAP simulado con Flask
@app.route('/soap', methods=['POST'])
def soap_service():
    """Endpoint principal para servicios SOAP"""
    try:
        xml_content = request.data.decode('utf-8')
        soap_data = extraer_soap_request(xml_content)
        operation = soap_data.get('operation', '')
        
        if operation == 'registrarNodo':
            # Registrar nodo
            data = {
                "ip": soap_data.get('ip'),
                "puertos": [int(p) for p in soap_data.get('puertos', '8001,8002,8004').split(',')],
                "capacidad_maxima": int(soap_data.get('capacidad_maxima', 100000)),
                "servicios": {
                    "procesamiento_batch": "8001",
                    "transformaciones_batch": "8002",
                    "conversion_unica": "8004"
                }
            }
            
            resultado = balanceador_client.registrar_nodo(data)
            return Response(
                crear_soap_response(resultado, 'registrarNodo'),
                mimetype='text/xml'
            )
            
        elif operation == 'procesarImagenes':
            # Procesar imágenes
            resultado = balanceador_client.procesar_imagenes(
                xml_content=soap_data.get('xml_content', ''),
                prioridad=int(soap_data.get('prioridad', 5)),
                tipo=soap_data.get('tipo_servicio', 'procesamiento_batch'),
                formato=soap_data.get('formato_salida', 'JPEG'),
                calidad=int(soap_data.get('calidad', 85))
            )
            return Response(
                crear_soap_response(resultado, 'procesarImagenes'),
                mimetype='text/xml'
            )
            
        elif operation == 'procesarImagenesAuto':
            # Procesar imágenes con polling automático
            poll_interval = float(soap_data.get('poll_interval', 5.0))
            max_attempts = int(soap_data.get('max_attempts', 60))
            resultado = asyncio.run(balanceador_client.procesar_imagenes_con_polling(
                xml_content=soap_data.get('xml_content', ''),
                prioridad=int(soap_data.get('prioridad', 5)),
                tipo=soap_data.get('tipo_servicio', 'procesamiento_batch'),
                formato=soap_data.get('formato_salida', 'JPEG'),
                calidad=int(soap_data.get('calidad', 85)),
                poll_interval=poll_interval,
                max_attempts=max_attempts
            ))
            return Response(
                crear_soap_response(resultado, 'procesarImagenesAuto'),
                mimetype='text/xml'
            )
            
        elif operation == 'obtenerResultado':
            # Obtener resultado
            task_id = soap_data.get('task_id', '')
            resultado = balanceador_client.obtener_resultado(task_id)
            return Response(
                crear_soap_response(resultado, 'obtenerResultado'),
                mimetype='text/xml'
            )
            
        elif operation == 'obtenerEstadisticas':
            # Obtener estadísticas
            resultado = balanceador_client.obtener_estadisticas()
            return Response(
                crear_soap_response(resultado, 'obtenerEstadisticas'),
                mimetype='text/xml'
            )
            
        elif operation == 'listarNodos':
            # Listar nodos
            resultado = balanceador_client.listar_nodos()
            return Response(
                crear_soap_response({"nodos": str(resultado)}, 'listarNodos'),
                mimetype='text/xml'
            )
            
        elif operation == 'healthCheck':
            # Health check
            resultado = balanceador_client.health_check()
            return Response(
                crear_soap_response(resultado, 'healthCheck'),
                mimetype='text/xml'
            )
            
        else:
            error_response = {"status": "error", "message": f"Operación no soportada: {operation}"}
            return Response(
                crear_soap_response(error_response, 'error'),
                mimetype='text/xml',
                status=400
            )
            
    except Exception as e:
        error_response = {"status": "error", "message": f"Error procesando SOAP: {str(e)}"}
        return Response(
            crear_soap_response(error_response, 'error'),
            mimetype='text/xml',
            status=500
        )

@app.route('/soap', methods=['GET'])
def wsdl():
    """Obtener WSDL del servicio SOAP"""
    if request.args.get('wsdl') is not None:
        wsdl_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:tns="http://servidor.procesamiento.imagenes/soap"
             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
             targetNamespace="http://servidor.procesamiento.imagenes/soap">

    <types>
        <schema xmlns="http://www.w3.org/2001/XMLSchema"
                targetNamespace="http://servidor.procesamiento.imagenes/soap">
            
            <element name="registrarNodo">
                <complexType>
                    <sequence>
                        <element name="ip" type="string"/>
                        <element name="puertos" type="string"/>
                        <element name="capacidad_maxima" type="int"/>
                    </sequence>
                </complexType>
            </element>
            
            <element name="procesarImagenes">
                <complexType>
                    <sequence>
                        <element name="xml_content" type="string"/>
                        <element name="prioridad" type="int"/>
                        <element name="tipo_servicio" type="string"/>
                        <element name="formato_salida" type="string"/>
                        <element name="calidad" type="int"/>
                    </sequence>
                </complexType>
            </element>
            
            <element name="procesarImagenesAuto">
                <complexType>
                    <sequence>
                        <element name="xml_content" type="string"/>
                        <element name="prioridad" type="int"/>
                        <element name="tipo_servicio" type="string"/>
                        <element name="formato_salida" type="string"/>
                        <element name="calidad" type="int"/>
                        <element name="poll_interval" type="float"/>
                        <element name="max_attempts" type="int"/>
                    </sequence>
                </complexType>
            </element>
            
            <element name="obtenerResultado">
                <complexType>
                    <sequence>
                        <element name="task_id" type="string"/>
                    </sequence>
                </complexType>
            </element>
            
        </schema>
    </types>

    <message name="registrarNodoRequest">
        <part name="parameters" element="tns:registrarNodo"/>
    </message>
    
    <message name="procesarImagenesRequest">
        <part name="parameters" element="tns:procesarImagenes"/>
    </message>
    
    <message name="procesarImagenesAutoRequest">
        <part name="parameters" element="tns:procesarImagenesAuto"/>
    </message>
    
    <message name="obtenerResultadoRequest">
        <part name="parameters" element="tns:obtenerResultado"/>
    </message>

    <portType name="BalanceadorSOAPPortType">
        <operation name="registrarNodo">
            <input message="tns:registrarNodoRequest"/>
        </operation>
        <operation name="procesarImagenes">
            <input message="tns:procesarImagenesRequest"/>
        </operation>
        <operation name="procesarImagenesAuto">
            <input message="tns:procesarImagenesAutoRequest"/>
        </operation>
        <operation name="obtenerResultado">
            <input message="tns:obtenerResultadoRequest"/>
        </operation>
    </portType>

    <binding name="BalanceadorSOAPBinding" type="tns:BalanceadorSOAPPortType">
        <soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
        
        <operation name="registrarNodo">
            <soap:operation soapAction="registrarNodo"/>
            <input><soap:body use="literal"/></input>
        </operation>
        
        <operation name="procesarImagenes">
            <soap:operation soapAction="procesarImagenes"/>
            <input><soap:body use="literal"/></input>
        </operation>
        
        <operation name="procesarImagenesAuto">
            <soap:operation soapAction="procesarImagenesAuto"/>
            <input><soap:body use="literal"/></input>
        </operation>
        
        <operation name="obtenerResultado">
            <soap:operation soapAction="obtenerResultado"/>
            <input><soap:body use="literal"/></input>
        </operation>
        
    </binding>

    <service name="BalanceadorSOAPService">
        <port name="BalanceadorSOAPPort" binding="tns:BalanceadorSOAPBinding">
            <soap:address location="http://localhost:8080/soap"/>
        </port>
    </service>

</definitions>'''
        
        return Response(wsdl_content, mimetype='text/xml')
    
    return "Servicio SOAP activo. Agregue ?wsdl para obtener la definición del servicio."

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

@app.route('/api/balanceador/registrar-nodo', methods=['POST'])
def rest_registrar_nodo():
    """REST: Registrar nodo"""
    data = request.get_json()
    resultado = balanceador_client.registrar_nodo(data)
    return jsonify(resultado)

@app.route('/api/balanceador/procesar', methods=['POST'])
def rest_procesar_imagenes():
    """REST: Procesar imágenes"""
    prioridad = int(request.args.get('prioridad', 5))
    tipo = request.args.get('tipo', 'procesamiento_batch')
    formato = request.args.get('formato', 'JPEG')
    calidad = int(request.args.get('calidad', 85))
    
    xml_content = request.data.decode('utf-8') if request.data else request.get_data(as_text=True)
    
    resultado = balanceador_client.procesar_imagenes(xml_content, prioridad, tipo, formato, calidad)
    return jsonify(resultado)

@app.route('/api/balanceador/procesar-auto', methods=['POST'])
async def rest_procesar_imagenes_auto():
    """REST: Procesar imágenes con polling automático"""
    prioridad = int(request.args.get('prioridad', 5))
    tipo = request.args.get('tipo', 'procesamiento_batch')
    formato = request.args.get('formato', 'JPEG')
    calidad = int(request.args.get('calidad', 85))
    poll_interval = float(request.args.get('poll_interval', 5.0))
    max_attempts = int(request.args.get('max_attempts', 60))
    
    xml_content = request.data.decode('utf-8') if request.data else request.get_data(as_text=True)
    
    resultado = await balanceador_client.procesar_imagenes_con_polling(
        xml_content, prioridad, tipo, formato, calidad, poll_interval, max_attempts
    )
    return jsonify(resultado)

@app.route('/api/balanceador/resultado/<task_id>', methods=['GET'])
def rest_obtener_resultado(task_id):
    """REST: Obtener resultado"""
    resultado = balanceador_client.obtener_resultado(task_id)
    return jsonify(resultado)

@app.route('/api/balanceador/estadisticas', methods=['GET'])
def rest_estadisticas():
    """REST: Estadísticas"""
    resultado = balanceador_client.obtener_estadisticas()
    return jsonify(resultado)

@app.route('/api/balanceador/nodos', methods=['GET'])
def rest_listar_nodos():
    """REST: Listar nodos"""
    resultado = balanceador_client.listar_nodos()
    return jsonify(resultado)

@app.route('/api/balanceador/health', methods=['GET'])
def rest_health_check():
    """REST: Health check"""
    resultado = balanceador_client.health_check()
    return jsonify(resultado)

@app.route('/api/servidor/estado', methods=['GET'])
def estado_servidor():
    """Estado del servidor de aplicación"""
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
            "rest_config": "/api/config/balanceador",
            "rest_proxy": "/api/balanceador/*"
        }
    })

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
                "operaciones": [
                    "registrarNodo",
                    "procesarImagenes", 
                    "procesarImagenesAuto",
                    "obtenerResultado",
                    "obtenerEstadisticas",
                    "listarNodos",
                    "healthCheck"
                ]
            },
            "rest": {
                "config_balanceador": "/api/config/balanceador",
                "estado_servidor": "/api/servidor/estado",
                "proxy_balanceador": "/api/balanceador/*"
            }
        },
        "balanceador": f"{IP_BALANCEADOR}:{PUERTO_BALANCEADOR}"
    })

def main():
    """Función principal"""
    print("🚀 Iniciando Servidor de Aplicación...")
    print("=" * 60)
    
    ip_local = socket.gethostbyname(socket.gethostname())
    puerto = 8080
    
    print("📡 Servicios SOAP disponibles:")
    print("  • registrarNodo - Registrar nodos")
    print("  • procesarImagenes - Procesar imágenes")
    print("  • procesarImagenesAuto - Procesar imágenes con polling automático")
    print("  • obtenerResultado - Obtener resultado")
    print("  • obtenerEstadisticas - Estadísticas")
    print("  • listarNodos - Listar nodos")
    print("  • healthCheck - Estado del balanceador")
    
    print(f"\n🌐 Endpoints:")
    print(f"  • SOAP: http://{ip_local}:{puerto}/soap")
    print(f"  • WSDL: http://{ip_local}:{puerto}/soap?wsdl")
    print(f"  • REST Proxy: http://{ip_local}:{puerto}/api/balanceador/*")
    print(f"  • REST Auto: http://{ip_local}:{puerto}/api/balanceador/procesar-auto")
    print(f"  • Config: http://{ip_local}:{puerto}/api/config/balanceador")
    
    print(f"\n⚙️  Balanceador: {IP_BALANCEADOR}:{PUERTO_BALANCEADOR}")
    print(f"⚡ Servidor listo en: {ip_local}:{puerto}")
    
    try:
        server = make_server(ip_local, puerto, app)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo servidor...")
        print("✅ Servidor detenido")

if __name__ == "__main__":
    main()