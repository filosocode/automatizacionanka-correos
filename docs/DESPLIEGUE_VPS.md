# Despliegue en una VPS (borrador, sin probar en Linux)

Objetivo: que el equipo entre por web (desde México) al dashboard de Anka en modo `api`. Requisito aparte: U-85.
**Estado (D-204: es el destino vigente):** los archivos de `deploy/` son una base. Aún no se han probado en una VPS real; ajusta rutas y verifica cada paso.

## Qué queda expuesto y qué no
- El dashboard solo escucha en `127.0.0.1:8765`. Lo único público es **Caddy** (puertos 80 y 443), con HTTPS y contraseña.
- **Nunca** abras el puerto 8765 al exterior. Sin la contraseña de Caddy, el dashboard no tiene inicio de sesión propio.
- Esto es contraseña compartida (sin roles ni registro de quién entró). Si necesitan saber quién hizo cada cambio, el
  paso siguiente es un inicio de sesión propio por usuario.

## Pasos
1. **VPS** con Ubuntu LTS y un dominio con un registro `A` hacia su IP.
2. **Firewall:** permitir solo 22 (SSH), 80 y 443 (`ufw allow 22,80,443/tcp && ufw enable`).
3. **Usuario y código:**
   `adduser --system --group anka`, copiar el proyecto a `/opt/anka`,
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock`.
4. **Variables:** copiar `deploy/anka.env.example` a `/etc/anka/anka.env` (`chmod 600`) con tu dominio y `OPENAI_API_KEY`.
5. **Modo:** en la `config.json` del servidor, `active_mode: api`. Los datos quedan en `~/.local/share/AnkaCorreos`
   del usuario `anka` (no hay `LOCALAPPDATA` en Linux).
6. **Servicio:** copiar `deploy/anka-dashboard.service` a `/etc/systemd/system/`,
   `systemctl enable --now anka-dashboard`.
7. **Caddy:** instalar, `caddy hash-password`, poner el hash en `deploy/Caddyfile` (copiado a `/etc/caddy/Caddyfile`),
   `systemctl reload caddy`. Caddy obtiene y renueva el certificado solo.
8. **Google (pendiente):** la autorización abre el navegador del equipo y en la VPS no hay navegador. Hasta resolverlo,
   autoriza en tu equipo y copia `token.json`, o define un flujo con la URL de retorno del dominio.
9. **Lectura de correos (pendiente):** el ciclo que revisa Gmail (`python app.py run --review-only`) debe correr cada pocos
   minutos con un temporizador de `systemd`. Falta definirlo y probarlo.

## Copias de seguridad
`anka/infra/backup.py` guarda copias en el mismo servidor. Programa además una copia diaria de la carpeta de datos fuera
de la VPS (otro servidor o un bucket) y prueba restaurarla una vez.

## Pendiente antes de dar acceso al equipo
- Correr la suite de pruebas en Linux (`python -m unittest discover -s tests -t . -q`).
- Resolver los puntos 8 y 9.
- Límite de gasto mensual en el panel de OpenAI.
