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
- **Los datos de clientes son sensibles**: el sistema corre **en línea, en un servidor propio de Anka**, y solo se entra por HTTPS con un inicio de sesión por usuario (U-85, U-115, `docs/DESPLIEGUE_VPS.md`). Los datos y las credenciales quedan en la carpeta de datos del servidor, fuera del repositorio; no se mandan a servicios gratuitos ni se imprimen en registros (D-204).
- **Todo cambio deja huella** (quién, cuándo, qué), y quien pide un cambio no puede desentenderse después.
- **El modo vigente es `api`** (D-203, D-204): es el que se prueba y se pone en línea. Los modos Ollama y ChatGPT Work siguen en el código y se comportan igual, pero no se evolucionan por ahora; al retomarlos se revisa que la redacción y las reglas coincidan.

## Cuándo está terminada la Fase 1
Un caso real recorre los 7 pasos sin salir del dashboard (salvo Gmail para leer y enviar), con evidencia de cada
hecho, y el comercial puede decir en cualquier momento en qué etapa está cada solicitud. La aceptación se prueba con
`docs/PILOTO_ACEPTACION_API.md` (modo API, el vigente; el de Work, `docs/PILOTO_ACEPTACION.md`, está en pausa); lo que Python no puede garantizar del modelo se registra como prueba manual.

## Fuera de alcance (por ahora)
Precios y presupuestos; generar la sección técnica T (lo hace Motor Anka, proyecto aparte); roles o permisos
distintos entre usuarios (U-115 identifica quién entra, pero todo usuario válido ve y hace lo mismo).

## Puesta en línea (D-204)
El destino es que el equipo entre por web al dashboard en modo `api`. La base existe (U-85, el proveedor de la API,
`deploy/` y `docs/DESPLIEGUE_VPS.md`) pero **no está probada en un servidor real**. Antes de dar acceso al equipo falta:
1. Correr la suite de pruebas en Linux.
2. Resolver la autorización de Google sin navegador en el servidor (hoy `authorize_interactive()` abre el navegador, D-27).
3. Programar la lectura de correos cada pocos minutos con un temporizador del sistema.
4. Copia diaria de la carpeta de datos **fuera** del servidor, y probar restaurarla una vez.
5. Límite de gasto mensual en el panel del proveedor de la API.
6. ~~Decidir si la contraseña compartida basta o hace falta un inicio de sesión por usuario~~ — decidido: por usuario (U-115, D-210), falta programarlo.
Cada punto que cambie comportamiento se escribe primero como requisito en `requisitos.md`.
