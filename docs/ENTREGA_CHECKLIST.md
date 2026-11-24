# Checklist de entrega profesional

## Imprescindible antes de entregar

- [ ] Definir servidor o computadora que quedara encendida.
- [ ] Definir usuario y ruta final del proyecto en el servidor.
- [ ] Configurar dominio o URL fija.
- [ ] Crear tunel Cloudflare permanente.
- [ ] Cambiar usuario y contrasena por defecto.
- [ ] Cambiar `CORTES_SECRET`.
- [ ] Configurar `GROUP_CONNECTOR_TOKEN` en panel y conector.
- [ ] Instalar dependencias Python.
- [ ] Instalar dependencias Node con `npm install`.
- [ ] Instalar Tesseract OCR y datos de idioma.
- [ ] Crear servicios automaticos para panel, conector y tunel.
- [ ] Crear backup automatico diario.
- [ ] Ejecutar `./deploy/check_status.sh` sin errores.
- [ ] Probar reinicio completo del equipo.
- [ ] Probar recepcion de imagen por WhatsApp.
- [ ] Probar exportacion CSV.
- [ ] Probar recuperacion de backup.

## Recomendado

- [ ] Pantalla visible de estado de WhatsApp.
- [ ] Dominio propio en vez de URL temporal.
- [ ] Certificados y acceso HTTPS gestionados por Cloudflare.
- [ ] Rotacion de logs.
- [ ] Politica de usuarios y contrasenas.
- [ ] Manual corto para operador.
- [ ] Manual tecnico para soporte.

## Riesgos conocidos

- WhatsApp Web puede pedir reconexion por QR.
- OCR puede fallar con imagenes borrosas.
- SQLite esta bien para operacion local, pero no para alta concurrencia.
- Si no hay backup externo, una falla del equipo puede perder datos.
- Cloudflare Tunnel rapido no garantiza URL permanente.
