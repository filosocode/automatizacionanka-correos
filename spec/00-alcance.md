# Alcance de la Fase 1

Sistema de apoyo comercial para Grupo Anka (topografía y geomática): recibe solicitudes de cotización por correo,
las ordena y deja el proyecto listo para **cotizar en una segunda etapa**. **No se cotiza** en esta fase: no hay precios,
presupuestos ni compromisos comerciales.

## Los 7 pasos
1. Leer correos. → **F1-01**
2. Identificar cuándo es una solicitud de cotización. → **F1-02**
3. Analizar la solicitud y resumirla en una tabla de actividades. → **F1-03**
4. Determinar si falta algún dato para la segunda etapa. → **F1-04**
5. Si falta, enviar un correo de respuesta a esa dirección. → **F1-05**
6. Esperar que regrese y terminar la tabla de alcances. → **F1-06**
7. Crear una carpeta en Google Drive (en la estructura de arranque ya creada). → **F1-07**

## Quién lo usa
Un comercial de Anka, no técnico en sistemas, con unos 5 a 20 casos activos. Quiere saber en segundos qué caso exige
acción y cuál es la siguiente.

## Principios que no se negocian
- **Nada sale al cliente sin que una persona lo revise y lo envíe** (R8). La única excepción son los recordatorios
  automáticos opcionales de R13.
- **No se inventa ningún dato**: lo que no está explícito se marca `no indicado`.
- **Los datos de clientes son sensibles**: el sistema funciona en local y no los expone.
- **Todo cambio deja huella** (quién, cuándo, qué), y quien pide un cambio no puede desentenderse después.
- **Los tres modos se comportan igual** (Ollama, API y ChatGPT Work).

## Cuándo está terminada la Fase 1
Un caso real recorre los 7 pasos sin salir del dashboard (salvo Gmail para leer y enviar), con evidencia de cada
hecho, y el comercial puede decir en cualquier momento en qué etapa está cada solicitud. La aceptación se prueba con
`docs/PILOTO_ACEPTACION.md`; lo que Python no puede garantizar de Work se registra como prueba manual.

## Fuera de alcance (por ahora)
Precios y presupuestos; generar la sección técnica T (lo hace Motor Anka, proyecto aparte); varios usuarios con
permisos distintos; despliegue en internet (se evaluó Tailscale y se dejó para después).
