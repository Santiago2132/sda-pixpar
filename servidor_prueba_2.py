import os
import threading
import time
import json
import socket
import subprocess
import re
from flask import Flask, request, Response
from flask_cors import CORS
import xml.etree.ElementTree as ET
import xmlrpc.client
from datetime import datetime
import uuid

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
    
    try:
        result = subprocess.run(['ip', 'route', 'get', '8.8.8.8'], 
                              capture_output=True, text=True, timeout=3)
        match = re.search(r'src (\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            return match.group(1)
    except:
        pass
    
    return "127.0.0.1"

# Configuración
BALANCEADOR_IP = "192.168.154.129"
BALANCEADOR_RPC_URL = f"http://{BALANCEADOR_IP}:8000"

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type", "SOAPAction", "Authorization"], methods=["GET", "POST", "OPTIONS"])

class SOAPImageService:
    """Servicio SOAP para procesamiento de imágenes"""
    
    def __init__(self):
        self.balanceador_client = None
        self.tareas_activas = {}  # task_id -> info
        self.lock = threading.Lock()
        self._conectar_balanceador()
        
        # Iniciar hilo de monitoreo de tareas
        threading.Thread(target=self._monitor_tareas, daemon=True).start()
    
    def _conectar_balanceador(self):
        """Conecta con el balanceador RPC"""
        try:
            self.balanceador_client = xmlrpc.client.ServerProxy(BALANCEADOR_RPC_URL)
            # Test de conectividad
            self.balanceador_client.ping()
            print(f"✅ Conectado al balanceador RPC: {BALANCEADOR_RPC_URL}")
        except Exception as e:
            print(f"❌ Error conectando con balanceador RPC: {e}")
            self.balanceador_client = None
    
    def _monitor_tareas(self):
        """Monitorea constantemente las tareas activas"""
        while True:
            try:
                if not self.balanceador_client:
                    self._conectar_balanceador()
                    time.sleep(5)
                    continue
                
                with self.lock:
                    tareas_a_verificar = list(self.tareas_activas.keys())
                
                for task_id in tareas_a_verificar:
                    try:
                        resultado_json = self.balanceador_client.obtener_resultado(task_id)
                        if resultado_json:
                            resultado = json.loads(resultado_json)
                            
                            with self.lock:
                                if task_id in self.tareas_activas:
                                    if resultado["status"] == "completado":
                                        self.tareas_activas[task_id]["status"] = "completado"
                                        self.tareas_activas[task_id]["xml_result"] = resultado["resultado"]
                                        self.tareas_activas[task_id]["tiempo_proceso"] = resultado.get("tiempo_proceso", 0)
                                        self.tareas_activas[task_id]["nodo_procesado"] = resultado.get("nodo_procesado", "")
                                        print(f"✅ Tarea {task_id} completada")
                                    elif resultado["status"] == "error":
                                        self.tareas_activas[task_id]["status"] = "error"
                                        self.tareas_activas[task_id]["error"] = resultado.get("error", "Error desconocido")
                                        print(f"❌ Tarea {task_id} falló: {resultado.get('error', 'Error desconocido')}")
                    
                    except Exception as e:
                        print(f"Error verificando tarea {task_id}: {e}")
                
                time.sleep(2)  # Verificar cada 2 segundos
                
            except Exception as e:
                print(f"Error en monitor de tareas: {e}")
                time.sleep(5)
    
    def procesar_imagenes_auto(self, xml_content, prioridad=5, tipo_servicio="procesamiento_batch", 
                              formato_salida="JPEG", calidad=85, poll_interval=3.0, max_attempts=30):
        """
        Procesa imágenes de forma automática con polling hasta completar
        """
        try:
            if not self.balanceador_client:
                self._conectar_balanceador()
                if not self.balanceador_client:
                    raise Exception("No se puede conectar con el balanceador")
            
            # Enviar tarea al balanceador
            task_id = self.balanceador_client.procesar_tarea(
                xml_content, prioridad, tipo_servicio, formato_salida, calidad
            )
            
            if not task_id:
                raise Exception("Error al crear tarea en el balanceador")
            
            # Registrar tarea
            with self.lock:
                self.tareas_activas[task_id] = {
                    "status": "procesando",
                    "timestamp": time.time(),
                    "xml_content": xml_content,
                    "prioridad": prioridad
                }
            
            print(f"🚀 Tarea {task_id} creada, iniciando polling...")
            
            # Polling hasta completar
            attempts = 0
            while attempts < max_attempts:
                time.sleep(poll_interval)
                attempts += 1
                
                with self.lock:
                    if task_id in self.tareas_activas:
                        tarea_info = self.tareas_activas[task_id]
                        
                        if tarea_info["status"] == "completado":
                            # Limpiar tarea completada
                            del self.tareas_activas[task_id]
                            return {
                                "success": True,
                                "task_id": task_id,
                                "xml_result": tarea_info["xml_result"],
                                "tiempo_proceso": tarea_info.get("tiempo_proceso", 0),
                                "nodo_procesado": tarea_info.get("nodo_procesado", ""),
                                "attempts": attempts
                            }
                        elif tarea_info["status"] == "error":
                            error_msg = tarea_info.get("error", "Error desconocido")
                            del self.tareas_activas[task_id]
                            return {
                                "success": False,
                                "error": error_msg,
                                "task_id": task_id
                            }
            
            # Timeout
            with self.lock:
                if task_id in self.tareas_activas:
                    del self.tareas_activas[task_id]
            
            return {
                "success": False,
                "error": f"Timeout después de {max_attempts} intentos",
                "task_id": task_id
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Error del servidor: {str(e)}"
            }
    
    def obtener_estadisticas(self):
        """Obtiene estadísticas del sistema"""
        try:
            if not self.balanceador_client:
                self._conectar_balanceador()
                if not self.balanceador_client:
                    return {"error": "No conectado al balanceador"}
            
            stats_json = self.balanceador_client.obtener_estadisticas()
            if stats_json:
                stats = json.loads(stats_json)
                
                # Agregar estadísticas del servidor SOAP
                with self.lock:
                    stats["servidor_soap"] = {
                        "tareas_activas_soap": len(self.tareas_activas),
                        "balanceador_conectado": self.balanceador_client is not None
                    }
                
                return stats
            else:
                return {"error": "No se pudieron obtener estadísticas"}
                
        except Exception as e:
            return {"error": f"Error obteniendo estadísticas: {str(e)}"}


# Instancia global del servicio
soap_service = SOAPImageService()

@app.route('/soap', methods=['POST', 'OPTIONS'])
def soap_endpoint():
    """Endpoint principal SOAP"""
    if request.method == 'OPTIONS':
        response = Response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type, SOAPAction, Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
        return response
    
    try:
        # Obtener contenido SOAP
        soap_content = request.data.decode('utf-8')
        
        # Parsear SOAP request
        soap_tree = ET.fromstring(soap_content)
        
        # Buscar el método solicitado
        body = soap_tree.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Body')
        if body is None:
            return crear_soap_fault("Client", "No se encontró el cuerpo SOAP")
        
        # Buscar operación
        operacion = None
        for child in body:
            if child.tag.endswith('}procesarImagenesAuto'):
                operacion = 'procesarImagenesAuto'
                break
            elif child.tag.endswith('}obtenerEstadisticas'):
                operacion = 'obtenerEstadisticas'
                break
        
        if not operacion:
            return crear_soap_fault("Client", "Operación no reconocida")
        
        # Ejecutar operación
        if operacion == 'procesarImagenesAuto':
            return manejar_procesar_imagenes_auto(body)
        elif operacion == 'obtenerEstadisticas':
            return manejar_obtener_estadisticas()
        
    except ET.ParseError as e:
        return crear_soap_fault("Client", f"SOAP XML malformado: {str(e)}")
    except Exception as e:
        return crear_soap_fault("Server", f"Error del servidor: {str(e)}")

def manejar_procesar_imagenes_auto(body):
    """Maneja la operación procesarImagenesAuto"""
    try:
        # Extraer parámetros SOAP
        ns = {'tns': 'http://servidor.procesamiento.imagenes/soap'}
        
        operacion_elem = body.find('.//{http://servidor.procesamiento.imagenes/soap}procesarImagenesAuto')
        
        xml_content = operacion_elem.findtext('.//tns:xml_content', '', ns)
        prioridad = int(operacion_elem.findtext('.//tns:prioridad', '5', ns))
        tipo_servicio = operacion_elem.findtext('.//tns:tipo_servicio', 'procesamiento_batch', ns)
        formato_salida = operacion_elem.findtext('.//tns:formato_salida', 'JPEG', ns)
        calidad = int(operacion_elem.findtext('.//tns:calidad', '85', ns))
        poll_interval = float(operacion_elem.findtext('.//tns:poll_interval', '3.0', ns))
        max_attempts = int(operacion_elem.findtext('.//tns:max_attempts', '30', ns))
        
        if not xml_content:
            return crear_soap_fault("Client", "xml_content requerido")
        
        # Validar XML
        try:
            ET.fromstring(xml_content)
        except:
            return crear_soap_fault("Client", "xml_content malformado")
        
        print(f"🎯 Procesando imágenes automáticamente - Prioridad: {prioridad}, Formato: {formato_salida}")
        
        # Procesar
        resultado = soap_service.procesar_imagenes_auto(
            xml_content=xml_content,
            prioridad=prioridad,
            tipo_servicio=tipo_servicio,
            formato_salida=formato_salida,
            calidad=calidad,
            poll_interval=poll_interval,
            max_attempts=max_attempts
        )
        
        if resultado["success"]:
            soap_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:procesarImagenesAutoResponse>
            <tns:status>success</tns:status>
            <tns:task_id>{resultado['task_id']}</tns:task_id>
            <tns:xml_result>{resultado['xml_result']}</tns:xml_result>
            <tns:tiempo_proceso>{resultado.get('tiempo_proceso', 0)}</tns:tiempo_proceso>
            <tns:nodo_procesado>{resultado.get('nodo_procesado', '')}</tns:nodo_procesado>
            <tns:attempts>{resultado.get('attempts', 0)}</tns:attempts>
        </tns:procesarImagenesAutoResponse>
    </soap:Body>
</soap:Envelope>"""
        else:
            soap_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:procesarImagenesAutoResponse>
            <tns:status>error</tns:status>
            <tns:error>{resultado['error']}</tns:error>
            <tns:task_id>{resultado.get('task_id', '')}</tns:task_id>
        </tns:procesarImagenesAutoResponse>
    </soap:Body>
</soap:Envelope>"""
        
        response = Response(soap_response)
        response.headers['Content-Type'] = 'text/xml; charset=utf-8'
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
        
    except Exception as e:
        return crear_soap_fault("Server", f"Error procesando imágenes: {str(e)}")

def manejar_obtener_estadisticas():
    """Maneja la operación obtenerEstadisticas"""
    try:
        estadisticas = soap_service.obtener_estadisticas()
        stats_json = json.dumps(estadisticas)
        
        soap_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:obtenerEstadisticasResponse>
            <tns:estadisticas>{stats_json}</tns:estadisticas>
        </tns:obtenerEstadisticasResponse>
    </soap:Body>
</soap:Envelope>"""
        
        response = Response(soap_response)
        response.headers['Content-Type'] = 'text/xml; charset=utf-8'
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
        
    except Exception as e:
        return crear_soap_fault("Server", f"Error obteniendo estadísticas: {str(e)}")

def crear_soap_fault(fault_code, fault_string):
    """Crea una respuesta SOAP Fault"""
    fault_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
    <soap:Body>
        <soap:Fault>
            <faultcode>{fault_code}</faultcode>
            <faultstring>{fault_string}</faultstring>
        </soap:Fault>
    </soap:Body>
</soap:Envelope>"""
    
    response = Response(fault_response, status=500)
    response.headers['Content-Type'] = 'text/xml; charset=utf-8'
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

@app.route('/soap', methods=['GET'])
def wsdl_endpoint():
    """Endpoint para WSDL"""
    server_ip = obtener_ip_real()
    
    wsdl_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:tns="http://servidor.procesamiento.imagenes/soap"
             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
             xmlns:xsd="http://www.w3.org/2001/XMLSchema"
             targetNamespace="http://servidor.procesamiento.imagenes/soap">

    <message name="procesarImagenesAutoRequest">
        <part name="xml_content" type="xsd:string"/>
        <part name="prioridad" type="xsd:int"/>
        <part name="tipo_servicio" type="xsd:string"/>
        <part name="formato_salida" type="xsd:string"/>
        <part name="calidad" type="xsd:int"/>
        <part name="poll_interval" type="xsd:float"/>
        <part name="max_attempts" type="xsd:int"/>
    </message>
    
    <message name="procesarImagenesAutoResponse">
        <part name="status" type="xsd:string"/>
        <part name="task_id" type="xsd:string"/>
        <part name="xml_result" type="xsd:string"/>
        <part name="error" type="xsd:string"/>
    </message>
    
    <message name="obtenerEstadisticasRequest"/>
    
    <message name="obtenerEstadisticasResponse">
        <part name="estadisticas" type="xsd:string"/>
    </message>

    <portType name="ImageProcessingPortType">
        <operation name="procesarImagenesAuto">
            <input message="tns:procesarImagenesAutoRequest"/>
            <output message="tns:procesarImagenesAutoResponse"/>
        </operation>
        <operation name="obtenerEstadisticas">
            <input message="tns:obtenerEstadisticasRequest"/>
            <output message="tns:obtenerEstadisticasResponse"/>
        </operation>
    </portType>

    <binding name="ImageProcessingBinding" type="tns:ImageProcessingPortType">
        <soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
        <operation name="procesarImagenesAuto">
            <soap:operation soapAction="procesarImagenesAuto"/>
            <input>
                <soap:body use="literal"/>
            </input>
            <output>
                <soap:body use="literal"/>
            </output>
        </operation>
        <operation name="obtenerEstadisticas">
            <soap:operation soapAction="obtenerEstadisticas"/>
            <input>
                <soap:body use="literal"/>
            </input>
            <output>
                <soap:body use="literal"/>
            </output>
        </operation>
    </binding>

    <service name="ImageProcessingService">
        <port name="ImageProcessingPort" binding="tns:ImageProcessingBinding">
            <soap:address location="http://{server_ip}:8080/soap"/>
        </port>
    </service>

</definitions>"""
    
    response = Response(wsdl_content)
    response.headers['Content-Type'] = 'text/xml; charset=utf-8'
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

@app.route('/health', methods=['GET'])
def health_check():
    """Health check del servidor SOAP"""
    return {
        "status": "healthy",
        "service": "Servidor SOAP - Procesamiento de Imágenes",
        "timestamp": time.time(),
        "balanceador_conectado": soap_service.balanceador_client is not None,
        "tareas_activas": len(soap_service.tareas_activas)
    }

def main():
    """Función principal"""
    print("🌐 Iniciando Servidor SOAP Intermediario...")
    print("=" * 50)
    
    # Obtener IP real
    ip_local = obtener_ip_real()
    puerto = 8080
    
    print("🎯 Configuración:")
    print(f"  • IP Servidor SOAP: {ip_local}:{puerto}")
    print(f"  • Balanceador RPC: {BALANCEADOR_IP}:8000")
    print(f"  • Cliente HTML: prueba.html -> SOAP -> este servidor -> RPC -> balanceador")
    
    print("\n🛠 Servicios disponibles:")
    print(f"  • POST /soap - Endpoint SOAP principal")
    print(f"  • GET /soap?wsdl - WSDL del servicio")
    print(f"  • GET /health - Health check")
    
    print("\n📋 Operaciones SOAP:")
    print("  • procesarImagenesAuto - Procesa imágenes con polling automático")
    print("  • obtenerEstadisticas - Obtiene estadísticas del sistema")
    
    print(f"\n🚀 Servidor SOAP ejecutándose en: {ip_local}:{puerto}")
    print("🔗 Comunicación: Cliente HTML <-SOAP-> Servidor <-RPC-> Balanceador")
    print("✅ CORS habilitado para desarrollo en VM")
    print("🎯 Servidor listo... (Ctrl+C para detener)")
    
    try:
        app.run(host=ip_local, port=puerto, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo servidor SOAP...")
        print("✅ Servidor detenido")

if __name__ == "__main__":
    main()