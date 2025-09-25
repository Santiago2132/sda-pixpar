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
import schedule

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

# CONFIGURACIÓN CORREGIDA - Usar IP dinámica
BALANCEADOR_IP = "192.168.154.129"
BALANCEADOR_RPC_URL = f"http://{BALANCEADOR_IP}:8000"
SERVIDOR_IP = obtener_ip_real()  # Detectar IP automáticamente

print(f"🔧 IP del servidor SOAP detectada: {SERVIDOR_IP}")

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type", "SOAPAction", "Authorization"], methods=["GET", "POST", "OPTIONS"])

class SOAPImageService:
    """Servicio SOAP para procesamiento de imágenes con recarga automática"""
    
    def __init__(self):
        self.balanceador_client = None
        self.tareas_activas = {}  # task_id -> info
        self.resultados_completados = {}  # task_id -> resultado (cache persistente)
        self.lock = threading.Lock()
        self._conectar_balanceador()
        
        # Iniciar hilos de monitoreo
        threading.Thread(target=self._monitor_tareas, daemon=True).start()
        threading.Thread(target=self._programar_recargas, daemon=True).start()
        
        # Configurar recarga automática cada 30 segundos
        schedule.every(30).seconds.do(self._recarga_periodica)
    
    def _conectar_balanceador(self):
        """Conecta con el balanceador RPC - VERSIÓN CORREGIDA"""
        try:
            print(f"🔄 Intentando conectar con balanceador: {BALANCEADOR_RPC_URL}")
            
            # CORREGIDO: Remover parámetro timeout que no existe en versiones anteriores
            self.balanceador_client = xmlrpc.client.ServerProxy(BALANCEADOR_RPC_URL)
            
            # Test de conectividad con timeout manual usando socket
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5)  # Timeout de 5 segundos
            
            try:
                response = self.balanceador_client.ping()
                if response == "pong":
                    print(f"✅ Conectado al balanceador RPC: {BALANCEADOR_RPC_URL}")
                else:
                    print(f"⚠️ Respuesta inesperada del balanceador: {response}")
                    self.balanceador_client = None
            finally:
                socket.setdefaulttimeout(old_timeout)  # Restaurar timeout original
                
        except ConnectionError as e:
            print(f"❌ Error de conexión con balanceador RPC: {e}")
            self.balanceador_client = None
        except socket.timeout:
            print(f"❌ Timeout conectando con balanceador RPC")
            self.balanceador_client = None
        except Exception as e:
            print(f"❌ Error conectando con balanceador RPC: {e}")
            print(f"   Verificar que el balanceador esté ejecutándose en {BALANCEADOR_IP}:8000")
            self.balanceador_client = None
    
    def _programar_recargas(self):
        """Ejecuta las recargas programadas"""
        while True:
            schedule.run_pending()
            time.sleep(1)
    
    def _recarga_periodica(self):
        """Recarga periódica del estado de las tareas"""
        print(f"🔄 Recarga automática iniciada - {datetime.now().strftime('%H:%M:%S')}")
        
        if not self.balanceador_client:
            print("⚠️ Cliente RPC no disponible, intentando reconectar...")
            self._conectar_balanceador()
            if not self.balanceador_client:
                print("❌ No se puede conectar al balanceador para recarga")
                return
        
        with self.lock:
            tareas_pendientes = [tid for tid, info in self.tareas_activas.items() 
                               if info["status"] == "procesando"]
        
        if tareas_pendientes:
            print(f"📋 Verificando {len(tareas_pendientes)} tareas pendientes...")
            
            for task_id in tareas_pendientes:
                self._verificar_tarea_individual(task_id)
        else:
            print("✅ No hay tareas pendientes para verificar")
    
    def _monitor_tareas(self):
        """Monitorea constantemente las tareas activas"""
        while True:
            try:
                if not self.balanceador_client:
                    self._conectar_balanceador()
                    time.sleep(5)
                    continue
                
                with self.lock:
                    tareas_a_verificar = [tid for tid, info in self.tareas_activas.items() 
                                        if info["status"] == "procesando"]
                
                for task_id in tareas_a_verificar:
                    self._verificar_tarea_individual(task_id)
                
                # Limpiar tareas antiguas (más de 1 hora)
                self._limpiar_tareas_antiguas()
                
                time.sleep(2)  # Verificar cada 2 segundos
                
            except Exception as e:
                print(f"Error en monitor de tareas: {e}")
                time.sleep(5)
    
    def _verificar_tarea_individual(self, task_id):
        """Verifica el estado de una tarea individual - VERSIÓN CORREGIDA"""
        try:
            # CORREGIDO: Usar timeout manual con socket
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(3)  # Timeout de 3 segundos para verificaciones
            
            try:
                resultado_json = self.balanceador_client.obtener_resultado(task_id)
                if resultado_json:
                    resultado = json.loads(resultado_json)
                    
                    with self.lock:
                        if task_id in self.tareas_activas:
                            if resultado["status"] == "completado":
                                # Mover a resultados completados
                                self.resultados_completados[task_id] = {
                                    "status": "completado",
                                    "xml_result": resultado["resultado"],
                                    "tiempo_proceso": resultado.get("tiempo_proceso", 0),
                                    "nodo_procesado": resultado.get("nodo_procesado", ""),
                                    "timestamp_completado": time.time()
                                }
                                # Actualizar tarea activa
                                self.tareas_activas[task_id].update(self.resultados_completados[task_id])
                                print(f"✅ Tarea {task_id} completada y cacheada")
                                
                            elif resultado["status"] == "error":
                                self.tareas_activas[task_id]["status"] = "error"
                                self.tareas_activas[task_id]["error"] = resultado.get("error", "Error desconocido")
                                print(f"❌ Tarea {task_id} falló: {resultado.get('error', 'Error desconocido')}")
            finally:
                socket.setdefaulttimeout(old_timeout)
        
        except xmlrpc.client.Fault as e:
            print(f"Error RPC verificando tarea {task_id}: {e}")
        except socket.timeout:
            print(f"Timeout verificando tarea {task_id}")
        except Exception as e:
            print(f"Error verificando tarea {task_id}: {e}")
            # Si hay error de conexión, marcar cliente como no disponible
            if "Connection" in str(e) or "timeout" in str(e).lower():
                print("🔄 Perdida conexión con balanceador, intentando reconectar...")
                self.balanceador_client = None
    
    def _limpiar_tareas_antiguas(self):
        """Limpia tareas antiguas para evitar acumulación de memoria"""
        tiempo_actual = time.time()
        tiempo_limite = 3600  # 1 hora
        
        with self.lock:
            # Limpiar tareas activas antiguas completadas
            tareas_a_limpiar = []
            for task_id, info in self.tareas_activas.items():
                if (tiempo_actual - info["timestamp"] > tiempo_limite and 
                    info["status"] in ["completado", "error"]):
                    tareas_a_limpiar.append(task_id)
            
            for task_id in tareas_a_limpiar:
                del self.tareas_activas[task_id]
                print(f"🗑️ Tarea antigua limpiada: {task_id}")
            
            # Limpiar cache de resultados (mantener solo las últimas 2 horas)
            resultados_a_limpiar = []
            for task_id, info in self.resultados_completados.items():
                if tiempo_actual - info.get("timestamp_completado", 0) > 7200:  # 2 horas
                    resultados_a_limpiar.append(task_id)
            
            for task_id in resultados_a_limpiar:
                del self.resultados_completados[task_id]
    
    def procesar_imagenes_auto(self, xml_content, prioridad=5, tipo_servicio="procesamiento_batch", 
                              formato_salida="JPEG", calidad=85, poll_interval=3.0, max_attempts=30):
        """Procesa imágenes de forma automática con polling hasta completar - VERSIÓN CORREGIDA"""
        try:
            if not self.balanceador_client:
                print("⚠️ Cliente RPC no disponible, intentando conectar...")
                self._conectar_balanceador()
                if not self.balanceador_client:
                    raise Exception("No se puede conectar con el balanceador")
            
            print(f"📨 Enviando tarea al balanceador - Prioridad: {prioridad}")
            
            # Enviar tarea al balanceador con timeout manual
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10)  # Timeout de 10 segundos para crear tarea
            
            try:
                task_id = self.balanceador_client.procesar_tarea(
                    xml_content, prioridad, tipo_servicio, formato_salida, calidad
                )
            finally:
                socket.setdefaulttimeout(old_timeout)
            
            if not task_id:
                raise Exception("Error al crear tarea en el balanceador")
            
            print(f"✅ Tarea creada exitosamente: {task_id}")
            
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
                
                # Verificar si ya está en cache
                with self.lock:
                    if task_id in self.resultados_completados:
                        resultado_cache = self.resultados_completados[task_id]
                        print(f"📦 Resultado obtenido desde cache en intento {attempts}")
                        return {
                            "success": True,
                            "task_id": task_id,
                            "xml_result": resultado_cache["xml_result"],
                            "tiempo_proceso": resultado_cache.get("tiempo_proceso", 0),
                            "nodo_procesado": resultado_cache.get("nodo_procesado", ""),
                            "attempts": attempts,
                            "from_cache": True
                        }
                    
                    if task_id in self.tareas_activas:
                        tarea_info = self.tareas_activas[task_id]
                        
                        if tarea_info["status"] == "completado":
                            print(f"✅ Tarea completada en intento {attempts}")
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
                            print(f"❌ Tarea falló en intento {attempts}: {error_msg}")
                            return {
                                "success": False,
                                "error": error_msg,
                                "task_id": task_id
                            }
                
                # Log de progreso cada 5 intentos
                if attempts % 5 == 0:
                    print(f"⏳ Polling... intento {attempts}/{max_attempts}")
            
            # Timeout
            print(f"⏰ Timeout después de {max_attempts} intentos")
            return {
                "success": False,
                "error": f"Timeout después de {max_attempts} intentos",
                "task_id": task_id
            }
            
        except xmlrpc.client.Fault as e:
            print(f"❌ Error RPC: {e}")
            return {
                "success": False,
                "error": f"Error RPC del balanceador: {str(e)}"
            }
        except socket.timeout:
            print(f"❌ Timeout en comunicación RPC")
            return {
                "success": False,
                "error": "Timeout en comunicación con balanceador"
            }
        except Exception as e:
            print(f"❌ Error del servidor SOAP: {e}")
            return {
                "success": False,
                "error": f"Error del servidor: {str(e)}"
            }
    
    def obtener_estado_tarea(self, task_id):
        """Obtiene el estado actual de una tarea específica"""
        with self.lock:
            # Buscar en resultados completados primero
            if task_id in self.resultados_completados:
                return self.resultados_completados[task_id]
            
            # Buscar en tareas activas
            if task_id in self.tareas_activas:
                return self.tareas_activas[task_id]
        
        return None
    
    def obtener_estadisticas(self):
        """Obtiene estadísticas del sistema"""
        try:
            if not self.balanceador_client:
                self._conectar_balanceador()
                if not self.balanceador_client:
                    return {"error": "No conectado al balanceador"}
            
            # CORREGIDO: Usar timeout manual con socket
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5)
            
            try:
                stats_json = self.balanceador_client.obtener_estadisticas()
            finally:
                socket.setdefaulttimeout(old_timeout)
                
            if stats_json:
                stats = json.loads(stats_json)
                
                # Agregar estadísticas del servidor SOAP
                with self.lock:
                    tareas_activas_count = len([t for t in self.tareas_activas.values() 
                                              if t["status"] == "procesando"])
                    tareas_completadas_count = len(self.resultados_completados)
                    
                    stats["servidor_soap"] = {
                        "tareas_activas_soap": tareas_activas_count,
                        "tareas_completadas_cache": tareas_completadas_count,
                        "total_tareas_registradas": len(self.tareas_activas),
                        "balanceador_conectado": self.balanceador_client is not None,
                        "recarga_automatica": True,
                        "servidor_ip": SERVIDOR_IP
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
            elif child.tag.endswith('}obtenerEstadoTarea'):
                operacion = 'obtenerEstadoTarea'
                break
        
        if not operacion:
            return crear_soap_fault("Client", "Operación no reconocida")
        
        # Ejecutar operación
        if operacion == 'procesarImagenesAuto':
            return manejar_procesar_imagenes_auto(body)
        elif operacion == 'obtenerEstadisticas':
            return manejar_obtener_estadisticas()
        elif operacion == 'obtenerEstadoTarea':
            return manejar_obtener_estado_tarea(body)
        
    except ET.ParseError as e:
        return crear_soap_fault("Client", f"SOAP XML malformado: {str(e)}")
    except Exception as e:
        return crear_soap_fault("Server", f"Error del servidor: {str(e)}")

def manejar_obtener_estado_tarea(body):
    """Maneja la operación obtenerEstadoTarea"""
    try:
        ns = {'tns': 'http://servidor.procesamiento.imagenes/soap'}
        operacion_elem = body.find('.//{http://servidor.procesamiento.imagenes/soap}obtenerEstadoTarea')
        
        task_id = operacion_elem.findtext('.//tns:task_id', '', ns)
        
        if not task_id:
            return crear_soap_fault("Client", "task_id requerido")
        
        estado = soap_service.obtener_estado_tarea(task_id)
        
        if not estado:
            soap_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:obtenerEstadoTareaResponse>
            <tns:status>not_found</tns:status>
            <tns:message>Tarea no encontrada</tns:message>
        </tns:obtenerEstadoTareaResponse>
    </soap:Body>
</soap:Envelope>"""
        else:
            estado_json = json.dumps(estado)
            soap_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:obtenerEstadoTareaResponse>
            <tns:status>found</tns:status>
            <tns:estado>{estado_json}</tns:estado>
        </tns:obtenerEstadoTareaResponse>
    </soap:Body>
</soap:Envelope>"""
        
        response = Response(soap_response)
        response.headers['Content-Type'] = 'text/xml; charset=utf-8'
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
        
    except Exception as e:
        return crear_soap_fault("Server", f"Error obteniendo estado de tarea: {str(e)}")

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
            from_cache = resultado.get("from_cache", False)
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
            <tns:from_cache>{str(from_cache).lower()}</tns:from_cache>
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
        <part name="from_cache" type="xsd:boolean"/>
    </message>
    
    <message name="obtenerEstadoTareaRequest">
        <part name="task_id" type="xsd:string"/>
    </message>
    
    <message name="obtenerEstadoTareaResponse">
        <part name="status" type="xsd:string"/>
        <part name="estado" type="xsd:string"/>
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
        <operation name="obtenerEstadoTarea">
            <input message="tns:obtenerEstadoTareaRequest"/>
            <output message="tns:obtenerEstadoTareaResponse"/>
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
        <operation name="obtenerEstadoTarea">
            <soap:operation soapAction="obtenerEstadoTarea"/>
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
            <soap:address location="http://{SERVIDOR_IP}:8080/soap"/>
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
    with soap_service.lock:
        tareas_activas = len([t for t in soap_service.tareas_activas.values() 
                            if t["status"] == "procesando"])
        cache_size = len(soap_service.resultados_completados)
    
    return {
        "status": "healthy",
        "service": "Servidor SOAP - Procesamiento de Imágenes",
        "timestamp": time.time(),
        "servidor_ip": SERVIDOR_IP,
        "balanceador_conectado": soap_service.balanceador_client is not None,
        "balanceador_url": BALANCEADOR_RPC_URL,
        "tareas_activas": tareas_activas,
        "resultados_cache": cache_size,
        "recarga_automatica": True
    }

@app.route('/reload', methods=['POST'])
def manual_reload():
    """Endpoint para recarga manual"""
    soap_service._recarga_periodica()
    return {"status": "reload_completed", "timestamp": time.time()}

@app.route('/test-connection', methods=['GET'])
def test_balanceador_connection():
    """Endpoint para probar conexión con balanceador"""
    try:
        if not soap_service.balanceador_client:
            soap_service._conectar_balanceador()
        
        if soap_service.balanceador_client:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(3)
            
            try:
                response = soap_service.balanceador_client.ping()
                return {
                    "status": "success",
                    "message": f"Conexión exitosa con balanceador: {response}",
                    "balanceador_url": BALANCEADOR_RPC_URL,
                    "timestamp": time.time()
                }
            finally:
                socket.setdefaulttimeout(old_timeout)
        else:
            return {
                "status": "error",
                "message": "No se pudo conectar con el balanceador",
                "balanceador_url": BALANCEADOR_RPC_URL,
                "timestamp": time.time()
            }, 500
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error probando conexión: {str(e)}",
            "balanceador_url": BALANCEADOR_RPC_URL,
            "timestamp": time.time()
        }, 500

def main():
    """Función principal"""
    print("🌐 Iniciando Servidor SOAP Mejorado...")
    print("=" * 50)
    
    puerto = 8080
    
    print("🎯 Configuración:")
    print(f"  • IP Servidor SOAP: {SERVIDOR_IP}:{puerto} (IP detectada automáticamente)")
    print(f"  • Balanceador RPC: {BALANCEADOR_IP}:8000")
    print(f"  • Recarga automática: cada 30 segundos")
    print(f"  • Cache persistente: habilitado")
    
    print("\n🛠 Servicios disponibles:")
    print(f"  • POST /soap - Endpoint SOAP principal")
    print(f"  • GET /soap?wsdl - WSDL del servicio")
    print(f"  • GET /health - Health check")
    print(f"  • POST /reload - Recarga manual")
    print(f"  • GET /test-connection - Probar conexión con balanceador")
    
    print("\n📋 Operaciones SOAP:")
    print("  • procesarImagenesAuto - Procesa imágenes con polling automático")
    print("  • obtenerEstadoTarea - Obtiene estado de una tarea específica")
    print("  • obtenerEstadisticas - Obtiene estadísticas del sistema")
    
    # Probar conexión inicial con balanceador
    print(f"\n🔗 Probando conexión inicial con balanceador...")
    if soap_service.balanceador_client:
        print("✅ Conexión inicial exitosa con balanceador")
    else:
        print("⚠️ No se pudo conectar inicialmente con el balanceador")
        print(f"   Verificar que el balanceador esté ejecutándose en {BALANCEADOR_IP}:8000")
    
    print(f"\n🚀 Servidor SOAP ejecutándose en: {SERVIDOR_IP}:{puerto}")
    print("🔗 Comunicación: Cliente <-SOAP-> Servidor <-RPC-> Balanceador")
    print("✅ CORS habilitado para desarrollo global")
    print("🔄 Recarga automática habilitada")
    print("🎯 Servidor listo... (Ctrl+C para detener)")
    
    try:
        app.run(host=SERVIDOR_IP, port=puerto, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo servidor SOAP...")
        print("✅ Servidor detenido")

if __name__ == "__main__":
    main()