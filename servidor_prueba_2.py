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
import base64

# Configuración del balanceador
IP_BALANCEADOR = "192.168.154.129"  # Cambiar por la IP real del balanceador
PUERTO_BALANCEADOR = 5000

app = Flask(__name__)
CORS(app, origins="*", methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "SOAPAction", "Authorization"])

class BalanceadorClient:
    """Cliente para comunicarse con el balanceador de cargas"""
    
    def __init__(self, ip_balanceador: str, puerto: int = 5000):
        self.base_url = f"http://{ip_balanceador}:{puerto}/api"
        self.timeout = 30
    
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
    
    async def procesar_con_polling(self, xml_content: str, prioridad: int = 5, 
                                  tipo: str = "procesamiento_batch", formato: str = "JPEG", 
                                  calidad: int = 85, poll_interval: float = 3.0, 
                                  max_attempts: int = 30) -> Dict[str, Any]:
        """Procesa imágenes con polling automático"""
        # Enviar tarea inicial
        result = self.procesar_imagenes(xml_content, prioridad, tipo, formato, calidad)
        if result.get("status") != "accepted" or "task_id" not in result:
            return {"status": "error", "message": result.get("message", "Error enviando tarea")}
        
        task_id = result["task_id"]
        attempts = 0
        
        while attempts < max_attempts:
            await asyncio.sleep(poll_interval)
            resultado = self.obtener_resultado(task_id)
            
            if resultado.get("status") == "success":
                return {
                    "status": "success",
                    "xml_result": resultado.get("resultado", ""),
                    "tiempo_proceso": resultado.get("tiempo_proceso"),
                    "nodo_procesado": resultado.get("nodo_procesado")
                }
            elif resultado.get("status") == "error":
                return {"status": "error", "message": resultado.get("message", "Error en procesamiento")}
            
            attempts += 1
        
        return {"status": "error", "message": f"Timeout después de {max_attempts} intentos"}

# Instancia del cliente del balanceador
balanceador_client = BalanceadorClient(IP_BALANCEADOR, PUERTO_BALANCEADOR)

def extraer_soap_request(xml_content: str) -> Dict[str, Any]:
    """Extrae y procesa datos de una petición SOAP correctamente"""
    try:
        # Limpiar el XML
        xml_content = xml_content.strip()
        if not xml_content:
            return {"error": "XML vacío"}
        
        # Parsear XML
        root = ET.fromstring(xml_content)
        
        # Namespaces comunes
        namespaces = {
            'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
            'tns': 'http://servidor.procesamiento.imagenes/soap'
        }
        
        # Buscar el body
        body = root.find('.//soap:Body', namespaces)
        if body is None:
            body = root.find('.//*[local-name()="Body"]')
        
        if body is None:
            return {"error": "No se encontró SOAP Body"}
        
        # Buscar la operación
        operation_element = None
        operation_name = ""
        
        for child in body:
            local_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if local_name.endswith('Response') or local_name in ['procesarImagenes', 'procesarImagenesAuto']:
                operation_element = child
                operation_name = local_name
                break
        
        if operation_element is None:
            return {"error": "No se encontró operación SOAP"}
        
        # Extraer parámetros
        data = {"operation": operation_name}
        
        for param in operation_element:
            param_name = param.tag.split('}')[-1] if '}' in param.tag else param.tag
            param_name = param_name.replace('tns:', '')
            data[param_name] = param.text if param.text else ""
        
        return data
        
    except ET.ParseError as e:
        return {"error": f"XML malformado: {str(e)}"}
    except Exception as e:
        return {"error": f"Error procesando SOAP: {str(e)}"}

def limpiar_xml_imagenes(xml_content: str) -> str:
    """Limpia y valida el XML de imágenes para el balanceador"""
    try:
        # Decodificar si viene escapado
        if '&lt;' in xml_content:
            xml_content = xml_content.replace('&lt;', '<').replace('&gt;', '>')
        
        # Intentar parsear como está
        try:
            root = ET.fromstring(xml_content)
            return xml_content
        except:
            pass
        
        # Si falla, puede estar anidado - extraer solo el contenido de imágenes
        start_marker = '<?xml version="1.0" encoding="UTF-8"?>'
        if start_marker in xml_content:
            xml_start = xml_content.find(start_marker)
            if xml_start >= 0:
                clean_xml = xml_content[xml_start:]
                try:
                    ET.fromstring(clean_xml)
                    return clean_xml
                except:
                    pass
        
        # Como último recurso, crear XML básico si encontramos elementos de imagen
        if '<imagen' in xml_content and '</imagen>' in xml_content:
            # Extraer todas las imágenes
            import re
            imagen_pattern = r'<imagen[^>]*>.*?</imagen>'
            imagenes = re.findall(imagen_pattern, xml_content, re.DOTALL)
            
            if imagenes:
                clean_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<imagenes>\n'
                for img in imagenes:
                    clean_xml += '    ' + img + '\n'
                clean_xml += '</imagenes>'
                return clean_xml
        
        raise Exception("No se pudo extraer XML válido")
        
    except Exception as e:
        raise Exception(f"Error limpiando XML: {str(e)}")

def crear_soap_response(data: Dict[str, Any], operation: str) -> str:
    """Crea una respuesta SOAP XML"""
    success = data.get("status") == "success" or data.get("success", False)
    
    response_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:tns="http://servidor.procesamiento.imagenes/soap">
    <soap:Body>
        <tns:{operation}Response>
            <tns:success>{str(success).lower()}</tns:success>'''
    
    if success:
        if "xml_result" in data:
            # Escapar el XML resultado
            xml_result = data["xml_result"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            response_xml += f'''
            <tns:xml_result>{xml_result}</tns:xml_result>'''
        
        for key in ["tiempo_proceso", "nodo_procesado", "task_id", "message"]:
            if key in data:
                response_xml += f'''
            <tns:{key}>{data[key]}</tns:{key}>'''
    else:
        error_msg = data.get("message", data.get("error", "Error desconocido"))
        response_xml += f'''
            <tns:error>{error_msg}</tns:error>'''
    
    response_xml += '''
        </tns:{}Response>
    </soap:Body>
</soap:Envelope>'''.format(operation)
    
    return response_xml

@app.route('/soap', methods=['POST', 'OPTIONS'])
def soap_service():
    """Endpoint principal para servicios SOAP"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Obtener contenido XML
        xml_content = request.data.decode('utf-8')
        if not xml_content:
            xml_content = request.get_data(as_text=True)
        
        print(f"SOAP Request recibido: {len(xml_content)} caracteres")
        
        # Extraer datos SOAP
        soap_data = extraer_soap_request(xml_content)
        
        if "error" in soap_data:
            print(f"Error extrayendo SOAP: {soap_data['error']}")
            return Response(
                crear_soap_response({"status": "error", "message": soap_data["error"]}, "error"),
                mimetype='text/xml',
                status=400
            )
        
        operation = soap_data.get('operation', '')
        print(f"Operación SOAP: {operation}")
        print(f"Parámetros: {list(soap_data.keys())}")
        
        if operation in ['procesarImagenes', 'procesarImagenesAuto']:
            # Extraer parámetros
            xml_imagenes = soap_data.get('xml_content', '')
            prioridad = int(soap_data.get('prioridad', 5))
            formato_salida = soap_data.get('formato_salida', 'JPEG')
            calidad = int(soap_data.get('calidad', 85))
            
            print(f"XML imágenes recibido: {len(xml_imagenes)} caracteres")
            
            if not xml_imagenes:
                return Response(
                    crear_soap_response({"status": "error", "message": "No se recibió XML de imágenes"}, operation),
                    mimetype='text/xml',
                    status=400
                )
            
            try:
                # Limpiar y validar XML
                xml_limpio = limpiar_xml_imagenes(xml_imagenes)
                print(f"XML limpio: {len(xml_limpio)} caracteres")
                
                # Procesar según la operación
                if operation == 'procesarImagenesAuto':
                    # Procesamiento con polling automático
                    poll_interval = float(soap_data.get('poll_interval', 3.0))
                    max_attempts = int(soap_data.get('max_attempts', 30))
                    
                    resultado = asyncio.run(balanceador_client.procesar_con_polling(
                        xml_content=xml_limpio,
                        prioridad=prioridad,
                        formato=formato_salida,
                        calidad=calidad,
                        poll_interval=poll_interval,
                        max_attempts=max_attempts
                    ))
                else:
                    # Procesamiento simple
                    resultado = balanceador_client.procesar_imagenes(
                        xml_content=xml_limpio,
                        prioridad=prioridad,
                        formato=formato_salida,
                        calidad=calidad
                    )
                
                print(f"Resultado del balanceador: {resultado.get('status', 'unknown')}")
                
                return Response(
                    crear_soap_response(resultado, operation),
                    mimetype='text/xml'
                )
                
            except Exception as e:
                error_msg = f"Error procesando imágenes: {str(e)}"
                print(f"Error: {error_msg}")
                return Response(
                    crear_soap_response({"status": "error", "message": error_msg}, operation),
                    mimetype='text/xml',
                    status=500
                )
        
        else:
            error_msg = f"Operación no soportada: {operation}"
            print(f"Error: {error_msg}")
            return Response(
                crear_soap_response({"status": "error", "message": error_msg}, "error"),
                mimetype='text/xml',
                status=400
            )
            
    except Exception as e:
        error_msg = f"Error general procesando SOAP: {str(e)}"
        print(f"Error: {error_msg}")
        return Response(
            crear_soap_response({"status": "error", "message": error_msg}, "error"),
            mimetype='text/xml',
            status=500
        )

@app.route('/soap', methods=['GET'])
def wsdl():
    """Obtener WSDL del servicio SOAP"""
    if request.args.get('wsdl') is not None:
        wsdl_content = '''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:tns="http://servidor.procesamiento.imagenes/soap"
             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
             targetNamespace="http://servidor.procesamiento.imagenes/soap">
    <service name="ImageProcessorService">
        <port name="ImageProcessorPort" binding="tns:ImageProcessorBinding">
            <soap:address location="http://192.168.154.130:8080/soap"/>
        </port>
    </service>
</definitions>'''
        
        return Response(wsdl_content, mimetype='text/xml')
    
    return "Servicio SOAP activo. Agregue ?wsdl para obtener la definición del servicio."

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Endpoint de prueba"""
    return jsonify({
        "status": "ok",
        "message": "Servidor SOAP funcionando",
        "balanceador": f"{IP_BALANCEADOR}:{PUERTO_BALANCEADOR}",
        "timestamp": time.time()
    })

@app.route('/', methods=['GET'])
def index():
    """Página principal"""
    return jsonify({
        "message": "Servidor SOAP - Procesamiento de Imágenes",
        "version": "2.0.0",
        "endpoints": {
            "soap": "/soap",
            "wsdl": "/soap?wsdl",
            "test": "/test"
        },
        "balanceador": f"{IP_BALANCEADOR}:{PUERTO_BALANCEADOR}"
    })

def main():
    """Función principal"""
    print("🚀 Iniciando Servidor SOAP (Versión Corregida)...")
    print("=" * 60)
    
    ip_local = socket.gethostbyname(socket.gethostname())
    puerto = 8080
    
    print(f"🌐 Endpoints:")
    print(f"  • SOAP: http://{ip_local}:{puerto}/soap")
    print(f"  • WSDL: http://{ip_local}:{puerto}/soap?wsdl")
    print(f"  • Test: http://{ip_local}:{puerto}/test")
    
    print(f"\n⚙️  Balanceador: {IP_BALANCEADOR}:{PUERTO_BALANCEADOR}")
    print(f"⚡ Servidor listo en: {ip_local}:{puerto}")
    print("🔧 CORS habilitado para todos los orígenes")
    print("📝 Debug mejorado para SOAP parsing")
    
    try:
        server = make_server(ip_local, puerto, app)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo servidor...")
        print("✅ Servidor detenido")

if __name__ == "__main__":
    main()