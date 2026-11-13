// Nodos que se juntan y se mueven detrás de todo (U-38, D-103): JavaScript puro en un <canvas>, sin librerías.
// Reproduce la matemática de tsparticles 3.9.1 tal como la usa el login de ANKA Inventario (ver D-103);
// la intensidad baja un poco porque aquí se trabaja, no se espera.
(function () {
  // <física> (sin DOM: tests/fisica_nodos_prueba.js la ejecuta en Node)
  const NODOS = { cantidad: 40, densidad: 2, areaBase: 1920 * 1080, minimo: 16, radioMin: 1.5, radioMax: 3.5,
                  distanciaLinea: 140, velocidad: 0.6, repulsion: 100, factorRepulsion: 100, maxEmpuje: 50,
                  fps: 60, maxDt: 100 };
  const facil = t => 1 - (1 - t) * (1 - t);                      // easeOutQuad
  function cuantos(ancho, alto) {                                  // densidad de tsparticles: 40 nodos en 1920x1080, proporcional al área; aquí x2 porque las tarjetas tapan buena parte
    const base = NODOS.cantidad * NODOS.densidad, meta = base * ancho * alto / NODOS.areaBase;
    return Math.max(NODOS.minimo, meta <= base ? Math.floor(meta) : Math.ceil(meta));
  }
  function nodoNuevo(ancho, alto, azar) {
    const angulo = azar() * Math.PI * 2;                           // dirección libre; vector unitario
    return { x: azar() * ancho, y: azar() * alto, vx: Math.cos(angulo), vy: Math.sin(angulo),
             r: NODOS.radioMin + azar() * (NODOS.radioMax - NODOS.radioMin), color: azar() < 0.5 ? 0 : 1 };
  }
  function ajustar(nodos, ancho, alto, azar) {
    const meta = cuantos(ancho, alto);
    while (nodos.length < meta) nodos.push(nodoNuevo(ancho, alto, azar));
    if (nodos.length > meta) nodos.splice(0, nodos.length - meta);
  }
  function repulsar(nodos, raton) {                                // empuja la posición, sin suavizar, en cada cuadro
    for (const n of nodos) {
      const dx = n.x - raton.x, dy = n.y - raton.y, d = Math.hypot(dx, dy);
      if (d > NODOS.repulsion) continue;
      const fuerza = Math.min(Math.max(facil(1 - d / NODOS.repulsion) * NODOS.factorRepulsion, 0), NODOS.maxEmpuje);
      n.x += d ? dx / d * fuerza : NODOS.factorRepulsion;
      n.y += d ? dy / d * fuerza : NODOS.factorRepulsion;
    }
  }
  function rebotar(n, ancho, alto) {                               // outModes: bounce (rebote total)
    const r = n.r;
    if (n.x - r > ancho) n.x = ancho - r; else if (n.x + r < 0) n.x = r;
    if (n.y - r > alto) n.y = alto - r; else if (n.y + r < 0) n.y = r;
    if (n.x + r >= ancho && n.vx > 0) { n.vx = -n.vx; n.x = ancho - r; } else if (n.x - r <= 0 && n.vx < 0) { n.vx = -n.vx; n.x = r; }
    if (n.y + r >= alto && n.vy > 0) { n.vy = -n.vy; n.y = alto - r; } else if (n.y - r <= 0 && n.vy < 0) { n.vy = -n.vy; n.y = r; }
  }
  // factor = 60 * milisegundos / 1000 (1 con 60 cuadros por segundo); la velocidad real es velocidad * factor / 2 px
  function avanzar(nodos, ancho, alto, factor, raton) {
    const paso = NODOS.velocidad * factor / 2;
    for (const n of nodos) { n.x += n.vx * paso; n.y += n.vy * paso; }
    if (raton) repulsar(nodos, raton);
    for (const n of nodos) rebotar(n, ancho, alto);
  }
  // </física>

  const lienzo = document.getElementById('particulas');
  if (!lienzo || !window.requestAnimationFrame || !window.matchMedia) return;
  if (document.body.dataset.fondo === 'simple') return;
  const OPACIDAD = 0.6, OPACIDAD_LINEA = 0.35, INTENSIDAD = 0.7, MAX_DPR = 2;
  const COLORES = { claro: ['#2f4d73', '#f47c27'], oscuro: ['#8fb0dc', '#f9a668'] };   // el oscuro aclara el azul: #2f4d73 casi no se ve sobre #0d1117
  const INTERVALO = 1000 / NODOS.fps;
  const sinMovimiento = matchMedia('(prefers-reduced-motion: reduce)');
  const sistemaOscuro = matchMedia('(prefers-color-scheme: dark)');
  const ctx = lienzo.getContext('2d');
  if (!ctx) return;
  let nodos = [], ancho = 0, alto = 0, cuadro = 0, ultimo = 0, oscuro = false;
  const raton = { x: 0, y: 0, activo: false };

  function esOscuro() {
    const tema = document.documentElement.dataset.theme;
    return tema === 'dark' || (tema !== 'light' && sistemaOscuro.matches);
  }
  function medir() {
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const nuevoAncho = window.innerWidth, nuevoAlto = window.innerHeight;
    lienzo.width = Math.round(nuevoAncho * dpr);
    lienzo.height = Math.round(nuevoAlto * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    for (const n of nodos) { n.x = ancho ? n.x * nuevoAncho / ancho : n.x; n.y = alto ? n.y * nuevoAlto / alto : n.y; }
    ancho = nuevoAncho; alto = nuevoAlto;
    ajustar(nodos, ancho, alto, Math.random);
  }
  function dibujar() {
    const paleta = oscuro ? COLORES.oscuro : COLORES.claro;
    const intensidad = oscuro ? Math.min(INTENSIDAD * 1.2, 1) : INTENSIDAD;   // sobre fondo oscuro hace falta algo más de opacidad
    ctx.clearRect(0, 0, ancho, alto);
    ctx.lineWidth = 1; ctx.strokeStyle = paleta[0];
    for (let i = 0; i < nodos.length; i++) {
      for (let j = i + 1; j < nodos.length; j++) {
        const d = Math.hypot(nodos[i].x - nodos[j].x, nodos[i].y - nodos[j].y);
        if (d > NODOS.distanciaLinea) continue;
        ctx.globalAlpha = (1 - d / NODOS.distanciaLinea) * OPACIDAD_LINEA * intensidad;   // más tenue cuanto más lejos
        ctx.beginPath(); ctx.moveTo(nodos[i].x, nodos[i].y); ctx.lineTo(nodos[j].x, nodos[j].y); ctx.stroke();
      }
    }
    ctx.globalAlpha = OPACIDAD * intensidad;
    for (const n of nodos) { ctx.fillStyle = paleta[n.color]; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill(); }
    ctx.globalAlpha = 1;
  }
  function siguiente(ahora) {
    cuadro = requestAnimationFrame(siguiente);
    const transcurrido = ahora - ultimo;
    if (transcurrido < INTERVALO - 2) return;            // tope de ~60 cuadros por segundo
    ultimo = ahora;
    // el movimiento sigue al reloj, no a los cuadros; un cuadro muy tardío no da un salto (máx. 100 ms)
    avanzar(nodos, ancho, alto, 60 * Math.min(transcurrido, NODOS.maxDt) / 1000, raton.activo ? raton : null);
    dibujar();
  }
  function arrancar() {
    if (cuadro || document.hidden || sinMovimiento.matches) return;
    if (document.hasFocus && !document.hasFocus()) return;      // sin el foco de la ventana no se anima (U-65)
    lienzo.hidden = false; oscuro = esOscuro();
    if (!nodos.length) medir();
    ultimo = performance.now();                           // reloj nuevo: al volver de una pestaña oculta no hay salto
    cuadro = requestAnimationFrame(siguiente);
  }
  function detener() { if (cuadro) cancelAnimationFrame(cuadro); cuadro = 0; }
  function apagar() { detener(); lienzo.hidden = true; nodos = []; }
  function escuchar(consulta, funcion) { consulta.addEventListener ? consulta.addEventListener('change', funcion) : consulta.addListener(funcion); }

  escuchar(sinMovimiento, () => sinMovimiento.matches ? apagar() : arrancar());
  escuchar(sistemaOscuro, () => { oscuro = esOscuro(); });
  new MutationObserver(() => { oscuro = esOscuro(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  document.addEventListener('visibilitychange', () => document.hidden ? detener() : arrancar());
  window.addEventListener('resize', () => { if (nodos.length) medir(); });
  // El ratón se escucha en window (el canvas no recibe eventos): el lienzo ocupa toda la ventana, así clientX/Y ya son sus coordenadas.
  window.addEventListener('mousemove', e => { raton.x = e.clientX; raton.y = e.clientY; raton.activo = true; }, { passive: true });
  const soltar = () => { raton.activo = false; };
  document.documentElement.addEventListener('mouseleave', soltar);
  document.addEventListener('mouseout', e => { if (!e.relatedTarget) soltar(); });
  window.addEventListener('blur', () => { soltar(); detener(); });      // sin el foco de la ventana no se anima (U-65)
  window.addEventListener('focus', arrancar);
  arrancar();
})();
// Evita el doble envío (U-31.18): un formulario que escribe se envía una sola vez y su botón queda bloqueado.
document.addEventListener('submit', event => {
  const formulario = event.target;
  if (event.defaultPrevented) return;      // otro script (p. ej. «no marcaste nada») ya lo detuvo: no se bloquea nada
  if (!formulario.method || formulario.method.toLowerCase() !== 'post') return;
  if (formulario.dataset.enviando === '1') { event.preventDefault(); return; }
  formulario.dataset.enviando = '1';
  const sender = event.submitter;
  // Toda acción que escribe dice qué está haciendo (U-37): lo ve quien mira y lo oye quien usa lector de pantalla.
  const textos = {'/send': 'Enviando…', '/draft': 'Guardando el borrador…', '/code-issue': 'Creando la carpeta y enviando…',
                  '/code': 'Emitiendo la T…', '/approve': 'Creando la carpeta…', '/refresh': 'Buscando…', '/hoja': 'Actualizando la hoja…',
                  '/activities': 'Guardando la tabla…', '/delete': 'Borrando…'};
  const destino = ((sender && sender.getAttribute('formaction')) || formulario.getAttribute('action') || '').split('?')[0];
  const texto = textos[destino] || 'Guardando…';
  const aviso = document.getElementById('cargando');
  formulario.setAttribute('aria-busy', 'true');
  if (aviso) { aviso.className = 'solo-lectores'; aviso.textContent = texto; }
  let original = null;
  if (sender) {
    setTimeout(() => {
      sender.disabled = true;
      original = sender.textContent;
      sender.textContent = texto;
    }, 0);
  }
  // Si el servidor no contesta, el botón no se queda gris para siempre (U-37).
  setTimeout(() => {
    if (!document.body.contains(formulario)) return;
    delete formulario.dataset.enviando;
    formulario.removeAttribute('aria-busy');
    if (sender) { sender.disabled = false; if (original !== null) sender.textContent = original; }
    if (aviso) {
      aviso.className = 'notice error';
      aviso.textContent = 'No hubo respuesta. Antes de intentarlo otra vez, mira el historial del caso: puede que sí se haya hecho.';
    }
  }, 30000);
});
(function () {
  // Con el mensaje del correo editado, salir de la página (un enlace, atrás, cerrar) pide confirmar (U-31.18).
  const cuerpo = document.getElementById('cuerpo-correo');
  if (!cuerpo) return;
  let enviando = false;
  document.addEventListener('submit', event => { if (event.target.method && event.target.method.toLowerCase() !== 'dialog') enviando = true; });
  window.addEventListener('beforeunload', event => {
    if (!enviando && cuerpo.value !== cuerpo.defaultValue) { event.preventDefault(); event.returnValue = ''; }
  });
})();
(function () {
  // «Editar tabla» y «+ Agregar» son botones: el primero abre y cierra el editor (aria-expanded) y el segundo lo abre y lleva
  // el cursor a la fila nueva (U-37). Sin JavaScript el editor se ve siempre (ver <noscript> arriba).
  const editor = document.getElementById('editar-tabla');
  const boton = document.getElementById('editar-boton'), agregar = document.getElementById('agregar-fila');
  const aviso = document.getElementById('editando-una');
  function abrirEditor(abrir) {
    editor.classList.toggle('abierto', abrir);
    boton.setAttribute('aria-expanded', String(abrir));
  }
  // Muestra solo una fila del editor (la que se edita o la nueva); sin fila, las muestra todas (U-105).
  function elegir(fila, texto) {
    editor.classList.toggle('solo-una', !!fila);
    editor.querySelectorAll('.act-row').forEach(f => f.classList.toggle('elegida', f === fila));
    if (aviso) { aviso.hidden = !fila; aviso.textContent = fila ? texto : ''; }
  }
  if (editor && boton) boton.addEventListener('click', () => {
    const abrir = boton.getAttribute('aria-expanded') !== 'true';
    elegir(null, '');
    abrirEditor(abrir);
  });
  if (editor && boton && agregar) agregar.addEventListener('click', () => {
    const nueva = editor.querySelector('.act-main.nueva');
    elegir(nueva ? nueva.closest('.act-row') : null, 'Agregando una actividad. Al guardar queda anotado con su motivo.');
    abrirEditor(true);
    const nuevas = editor.querySelectorAll('.act-main.nueva textarea');
    editor.scrollIntoView({block: 'start'});
    if (nuevas.length) nuevas[0].focus({preventScroll: true});
  });
  // Panel de una actividad (U-112.B): se abre bajo la lista y cambia lo que hay que hacer; los filtros solo existen con JS.
  const paneles = document.getElementById('paneles-actividad');
  const filtros = document.getElementById('filtros-actividades');
  // El panel ocupa el contenedor de la derecha (el del correo) y lo devuelve al cerrarse; sin dos columnas queda bajo la lista (U-112.A.4).
  const derecha = document.querySelector('.dos-columnas > .section:not(.actividades)');
  let origenDelPanel = null;     // el botón que abrió el panel: al volver al correo, el foco regresa a él
  function cerrarPanel() {
    document.querySelectorAll('.panel-actividad').forEach(p => { p.hidden = true; if (paneles) paneles.appendChild(p); });
    document.querySelectorAll('.abrir-panel.abierto, #editar-boton.abierto, #agregar-fila.abierto').forEach(b => b.classList.remove('abierto'));
    if (derecha) {
      const sitio = derecha.querySelector('.panel-sitio');
      if (sitio) sitio.remove();
      derecha.classList.remove('con-panel');
    }
  }
  function abrirPanel(id, boton) {
    const panel = document.getElementById(id);
    if (!panel) return;
    cerrarPanel();
    panel.hidden = false;
    if (boton) boton.classList.add('abierto');
    if (derecha) {
      const sitio = document.createElement('div');
      sitio.className = 'panel-sitio';
      sitio.appendChild(panel);
      derecha.insertBefore(sitio, derecha.firstChild);
      derecha.classList.add('con-panel');
    }
    panel.scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    origenDelPanel = boton || null;
    // El foco va al título del panel: el lector de pantalla dice qué se abrió y desde ahí se sigue con Tab.
    const titulo = panel.querySelector('h3');
    if (titulo) { titulo.tabIndex = -1; titulo.focus({preventScroll: true}); }
  }
  if (paneles) {
    document.querySelectorAll('.abrir-panel').forEach(b => b.addEventListener('click', () => abrirPanel(b.dataset.panel, b)));
    // Como en el prototipo guiado: tocar el renglón (no su casilla ni su botón) también abre el panel de la actividad.
    document.querySelectorAll('.renglon').forEach(r => r.addEventListener('click', e => {
      if (e.target.closest('label, input, button, a')) return;
      const b = r.querySelector('.abrir-panel');
      if (b) b.click();
    }));
    document.querySelectorAll('.cerrar-panel').forEach(b => b.addEventListener('click', () => {
      const vuelta = origenDelPanel;
      cerrarPanel();
      if (vuelta && vuelta.isConnected) vuelta.focus({preventScroll: true});
    }));
    document.querySelectorAll('.panel-si').forEach(b => b.addEventListener('click', () => {
      const panel = b.closest('.panel-actividad');
      panel.querySelector('.form-panel').hidden = false;
      b.setAttribute('aria-expanded', 'true');
      const campo = panel.querySelector('.form-panel textarea, .form-panel input:not([type=hidden])');
      if (campo) campo.focus({preventScroll: true});
    }));
    document.querySelectorAll('.panel-no').forEach(b => b.addEventListener('click', () => {
      const casilla = document.querySelector('#renglon-' + b.dataset.casilla + ' input[name="preguntar_fila"]');
      if (casilla && !casilla.checked) { casilla.checked = true; casilla.dispatchEvent(new Event('change')); }
      cerrarPanel();
    }));
    // «Corregir» y «Agregar» viven dentro de «Gestionar tabla»: al volver, el foco regresa a ese botón.
    const gestionar = document.getElementById('gestionar-tabla');
    const volverA = () => { if (gestionar) origenDelPanel = gestionar; };
    // «Corregir la tabla» abre su panel en el contenedor del correo, igual que «Resolver»; la tabla densa queda solo sin JavaScript.
    if (boton) boton.addEventListener('click', evento => {
      evento.stopImmediatePropagation();
      if (boton.classList.contains('abierto')) cerrarPanel(); else { abrirPanel('panel-corregir', boton); volverA(); }
    }, true);
    // «Agregar» abre el panel con respaldo.
    if (agregar) agregar.addEventListener('click', evento => {
      evento.stopImmediatePropagation();
      abrirPanel('panel-nueva', agregar);
      volverA();
    }, true);
    // El respaldo muestra solo lo que corresponde al origen elegido y avisa antes de enviar (mismos textos que el servidor).
    document.querySelectorAll('fieldset.respaldo').forEach(caja => {
      const mostrar = () => {
        const elegido = (caja.querySelector('input[name="origen"]:checked') || {}).value;
        caja.querySelectorAll('[data-origen]').forEach(parte => { parte.hidden = parte.dataset.origen !== elegido; });
      };
      caja.querySelectorAll('input[name="origen"]').forEach(r => r.addEventListener('change', mostrar));
      const formulario = caja.closest('form');
      formulario.addEventListener('submit', evento => {
        const valor = nombre => {
          const marcado = caja.querySelector('input[type="radio"][name="' + nombre + '"]');
          if (marcado) return ((caja.querySelector('input[name="' + nombre + '"]:checked') || {}).value || '').trim();
          return ((caja.querySelector('[name="' + nombre + '"]') || {}).value || '').trim();
        };
        const origen = (caja.querySelector('input[name="origen"]:checked') || {}).value;
        let error = '';
        if (!origen) error = 'Elige de dónde viene: el cliente, un documento o tu criterio técnico.';
        else if (origen === 'cliente' && !valor('medio')) error = 'Elige por qué medio te lo dijo el cliente.';
        else if (origen === 'cliente' && !valor('fecha')) error = 'Escribe el día en que te lo dijo el cliente.';
        else if (origen === 'documento' && !valor('documento')) error = 'Elige en cuál documento está.';
        else if (valor('nota').replace(/\s+/g, ' ').length < 10) error = 'Cuéntalo en una frase (mínimo 10 letras), para que quede claro de dónde sale.';
        const linea = caja.querySelector('.error-respaldo');
        linea.hidden = !error;
        linea.textContent = error;
        if (error) { evento.preventDefault(); linea.scrollIntoView({block: 'nearest'}); }
      });
    });
    // Filtros por estado: si uno no deja ninguna actividad a la vista, se vuelve a «Todas».
    if (filtros) {
      filtros.hidden = false;
      const aviso = document.getElementById('sin-coincidencias');
      filtros.querySelectorAll('.chip-filtro').forEach(chip => chip.addEventListener('click', () => {
        const quiere = chip.dataset.filtro;
        let vistas = 0;
        document.querySelectorAll('#lista-actividades .renglon').forEach(r => {
          const ver = quiere === 'todas' || r.dataset.estado === quiere;
          r.hidden = !ver;
          if (ver) vistas += 1;
        });
        filtros.querySelectorAll('.chip-filtro').forEach(c => {
          const activo = c === chip;
          c.classList.toggle('activo', activo);
          c.setAttribute('aria-pressed', String(activo));
        });
        if (aviso) aviso.hidden = vistas > 0;
      }));
    }
    // Tras un rechazo, el panel de esa actividad vuelve a abrirse con lo escrito.
    const reabrir = paneles.dataset.reabrir;
    if (reabrir) {
      const panel = document.getElementById(reabrir);
      if (panel) {
        let escrito = {};
        try { escrito = JSON.parse(paneles.dataset.escrito || '{}'); } catch (e) { escrito = {}; }
        const formulario = panel.querySelector('input[name="accion"][value="' + (paneles.dataset.accion || '') + '"]');
        const caja = formulario ? formulario.closest('form') : panel;
        Object.keys(escrito).forEach(nombre => {
          caja.querySelectorAll('[name="' + nombre + '"]').forEach(campo => {
            if (campo.type === 'radio') campo.checked = campo.value === escrito[nombre];
            else if (campo.type !== 'hidden') campo.value = escrito[nombre];
          });
        });
        caja.querySelectorAll('input[name="origen"]').forEach(r => { if (r.checked) r.dispatchEvent(new Event('change')); });
        const detalles = caja.closest('details');
        if (detalles) detalles.open = true;
        const si = panel.querySelector('.panel-si');
        if (si) si.click();
        abrirPanel(reabrir, document.querySelector('[data-panel="' + reabrir + '"]'));
      }
    }
  }
  document.querySelectorAll('.editar-fila').forEach(b => b.addEventListener('click', () => {
    if (!editor || !boton) return;
    const fila = editor.querySelectorAll('.act-row')[Number(b.dataset.fila)];
    if (!fila) return;
    const nombre = (fila.querySelector('textarea') || {}).value || 'la actividad';
    elegir(fila, 'Editando: ' + nombre + '. Al guardar queda anotado con su motivo.');
    abrirEditor(true);
    const mas = fila.querySelector('.act-more details');
    if (mas) mas.open = true;          // lo que suele faltar (ubicación, entregable, plazo) queda a la vista
    editor.scrollIntoView({block: 'start'});
    const campo = fila.querySelector('textarea.vacio') || fila.querySelector('textarea');
    if (campo) campo.focus({preventScroll: true});
  }));
  // «Completar la tabla yo» (caso sin análisis de Work) y el enlace #editar-tabla del Resumen abren el editor existente.
  const completar = document.getElementById('completar-tabla');
  if (editor && agregar && completar) completar.addEventListener('click', evento => { evento.preventDefault(); agregar.click(); });
  if (editor && agregar && location.hash === '#editar-tabla') agregar.click();
  // Las casillas «¿Preguntar?» de la tabla ponen o quitan su pregunta en el cuadro único de preguntas (U-103.7).
  const cuadroPreguntas = document.querySelector('textarea[name="questions"]');
  const clave = texto => texto.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
  document.querySelectorAll('input[name="preguntar_fila"]').forEach(casilla => casilla.addEventListener('change', () => {
    if (!cuadroPreguntas) return;
    const lineas = cuadroPreguntas.value.split('\n');
    if (casilla.checked) {
      if (!lineas.some(linea => clave(linea) === clave(casilla.value))) {
        cuadroPreguntas.value = (cuadroPreguntas.value.trim() ? cuadroPreguntas.value.replace(/\s+$/, '') + '\n' : '') + casilla.value;
      }
    } else {
      cuadroPreguntas.value = lineas.filter(linea => clave(linea) !== clave(casilla.value)).join('\n');
    }
  }));
  // Esc cierra el menú Ajustes y devuelve el foco a su título.
  const ajustes = document.getElementById('ajustes');
  if (ajustes) ajustes.addEventListener('keydown', evento => {
    if (evento.key !== 'Escape' || !ajustes.open) return;
    ajustes.open = false;
    ajustes.querySelector('summary').focus();
  });
  const mensaje = document.querySelector('.notice.flash');
  if (mensaje) { mensaje.tabIndex = -1; mensaje.focus({preventScroll: true}); }     // el foco va al mensaje, no al inicio (U-31.25)
  const direccion = new URL(location.href);
  if (direccion.searchParams.has('message')) {
    direccion.searchParams.delete('message');
    history.replaceState(null, '', direccion.pathname + direccion.search + direccion.hash);
  }
})();
(function () {
  const cuerpo = document.getElementById('cuerpo-correo'), aviso = document.getElementById('aviso-espera');
  if (!cuerpo || !aviso) return;
  const casilla = document.getElementById('esperar-respuesta');
  function contar() {
    // Misma regla que el servidor (anka/dominio/preguntas.py): los puntos de la lista que presenta «confirmar lo
    // siguiente» y las preguntas con «?» fuera de ella, sin contar enlaces.
    const plano = t => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
    const texto = cuerpo.value.replace(/https?:\/\/\S+|www\.\S+/gi, '');
    const lineas = texto.split('\n');
    const puntos = [];
    lineas.forEach((linea, i) => {
      if (!/confirm(?:ar|e|en|arnos)\s+lo\s+siguiente/.test(plano(linea))) return;
      let hay = false;
      for (const siguiente of lineas.slice(i + 1)) {
        if (!siguiente.trim()) { if (hay) break; continue; }
        const punto = siguiente.match(/^\s*(?:\d+\s*[.)]|[-*\u2022])\s+(\S.*?)\s*$/);
        if (!punto) break;
        hay = true;
        puntos.push(plano(punto[1]));
      }
    });
    (texto.match(/¿[^?¿]*\?|[^\n¿.!?]{6,}\?/g) || []).map(plano).forEach(h => {
      if (h !== '¿podria confirmar lo siguiente?' && !puntos.some(p => p.includes(h))) puntos.push(h);
    });
    return new Set(puntos).size;
  }
  function mostrar() {
    const n = contar();
    const cajaEsperar = document.getElementById('caja-esperar');
    if (cajaEsperar) cajaEsperar.hidden = n > 0;      // solo se ofrece cuando el mensaje no lleva preguntas (U-31)
    const texto = n ? 'Este correo lleva ' + n + ' pregunta' + (n === 1 ? '' : 's') + ': el caso quedará esperando al cliente.'
      : (casilla.checked ? 'Este correo no lleva preguntas, pero marcaste esperar su respuesta: el caso quedará esperando al cliente.'
      : 'Este correo no lleva preguntas: no se espera respuesta del cliente.');
    if (aviso.textContent !== texto) aviso.textContent = texto;       // solo si cambió: el lector no repite lo mismo en cada tecla
  }
  cuerpo.addEventListener('input', mostrar); casilla.addEventListener('change', mostrar); mostrar();
  // Rearmar el correo con otras preguntas reemplaza el cuadro: si ya lo editaste, se pide confirmar (U-80).
  const armar = document.getElementById('armar-correo');
  if (armar) cuerpo.addEventListener('input', () => {
    armar.dataset.confirm = 'El correo se volverá a armar con estas preguntas y se reemplazará lo que escribiste en el cuadro. ¿Continuar?';
  }, {once: true});
})();
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload();
});
(function () {
  // Confirmaciones (U-80): un <dialog> propio con el texto de data-confirm y, en el envío, a quién va y qué lleva.
  // Sin <dialog> queda el confirm() del navegador; sin JavaScript el botón actúa como siempre.
  const dialogo = document.getElementById('confirmar');
  const nativo = !dialogo || typeof dialogo.showModal !== 'function';
  const detalle = document.getElementById('confirmar-detalle');
  const aceptar = document.getElementById('confirmar-si');
  let origen = null;
  // Un mensaje citado de verdad (cabecera con fecha o correo, o varias líneas con «>»): se avisa, nunca se recorta (U-39).
  function citaEn(texto) {
    return /^[ \t]*(El|On)\s[^\n]*(\d|@)[^\n]*(escribi[óo]|wrote)[ \t]*:[ \t]*$/im.test(texto) || /^[ \t]*>[^\n]*\n[ \t]*>/m.test(texto);
  }
  function avisoDeCita(boton, filas) {
    const campo = boton.form && boton.form.elements['body'];
    if (campo && citaEn(campo.value.replace(/\r\n/g, '\n'))) filas.push(['Ojo', 'Tu respuesta incluye un mensaje citado. ¿Enviar así?']);
    return filas;
  }
  function resumenDe(boton) {
    if (boton.dataset.resumen === 'emision') {      // La T no se deshace: se muestra lo que se va a emitir (U-34.2)
      const campo = boton.form && boton.form.elements['number'];
      const numero = campo ? campo.value.trim() : '';
      const filas = [['Número de la T', numero ? 'T' + numero : '(vacío)'], ['Para', boton.dataset.para || '']];
      if (boton.dataset.sugerido && numero !== boton.dataset.sugerido) filas.push(['Ojo', 'El número no es el sugerido (T' + boton.dataset.sugerido + '). Confirma que es el consecutivo real.']);
      if (boton.dataset.anio && numero.slice(0, 2) !== boton.dataset.anio) filas.push(['Ojo', 'El año del número no es el actual (' + boton.dataset.anio + ').']);
      return avisoDeCita(boton, filas);
    }
    if (boton.dataset.resumen !== 'envio') return [];
    const filas = [['Para', boton.dataset.para || '']];
    if (boton.dataset.asunto) filas.push(['Asunto', boton.dataset.asunto]);
    const aviso = document.getElementById('aviso-espera');
    if (aviso && aviso.textContent) filas.push(['Preguntas', aviso.textContent]);
    filas.push(['Adjuntos', 'Ninguno: la respuesta va sin archivos']);
    filas.push(['Después', 'El caso queda en «Esperando al cliente». No tienes que hacer nada más: si no responde, te avisamos para el recordatorio.']);
    return avisoDeCita(boton, filas);
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-print]')) window.print();
    const boton = event.target.closest('[data-confirm]');
    if (!boton) return;
    if (boton === origen && boton.dataset.confirmado === '1') { delete boton.dataset.confirmado; origen = null; return; }
    if (nativo) { if (!confirm(boton.dataset.confirm)) event.preventDefault(); return; }
    event.preventDefault();
    origen = boton;
    document.getElementById('confirmar-texto').textContent = boton.dataset.confirm;
    detalle.replaceChildren();
    const filas = resumenDe(boton);
    filas.forEach(([clave, valor]) => {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = clave; dd.textContent = valor;
      detalle.append(dt, dd);
    });
    detalle.hidden = !filas.length;
    aceptar.textContent = boton.dataset.confirmBoton || 'Confirmar';
    dialogo.returnValue = '';
    dialogo.showModal();
  });
  if (nativo) return;
  dialogo.addEventListener('close', () => {
    const boton = origen;
    if (!boton) return;
    if (dialogo.returnValue === 'si') { boton.dataset.confirmado = '1'; boton.click(); }
    else { origen = null; boton.focus(); }
  });
})();
(function () {
  // Búsqueda de «Mis casos» (U-49): el servidor busca entre todos los casos al pulsar «Buscar» o Intro. Mientras se
  // escribe, esto solo afina al instante las filas que ya están en la página; sin JavaScript queda el servidor.
  const buscador = document.getElementById('buscar-casos');
  if (!buscador) return;
  const filas = Array.from(document.querySelectorAll('#casos .queue-row'));
  const cuenta = document.getElementById('n-visibles');
  const vacio = document.getElementById('sin-casos');
  const textoInicial = cuenta ? cuenta.textContent : '';
  const buscado = buscador.value.trim().toLocaleLowerCase('es');
  const hayMas = Boolean(document.querySelector('.ver-mas'));
  buscador.addEventListener('input', () => {
    const termino = buscador.value.trim().toLocaleLowerCase('es');
    const afinar = termino !== buscado;              // lo que ya busca el servidor no se vuelve a filtrar
    let visibles = 0;
    filas.forEach(fila => {
      const ver = !afinar || (fila.dataset.search || '').includes(termino);
      fila.hidden = !ver;
      if (ver) visibles += 1;
    });
    if (vacio) vacio.hidden = visibles !== 0;
    if (!cuenta) return;
    let texto = textoInicial;
    if (afinar) {
      texto = visibles === 0 ? 'Ningún caso coincide' : visibles + (visibles === 1 ? ' caso a la vista' : ' casos a la vista');
      if (hayMas) texto += ' entre los que se muestran. Pulsa Buscar para mirar todos.';
    }
    if (cuenta.textContent !== texto) cuenta.textContent = texto;
  });
})();
(function () {
  // «Administrar casos»: la búsqueda, la etapa, el tipo y el orden los resuelve el servidor sobre todos los casos
  // (U-49). Aquí: afinar al escribir las filas ya cargadas, enviar al cambiar el orden o el tipo, abrir los grupos y
  // las casillas de borrar y de descartar avisos en lote.
  const search = document.querySelector('#search');
  if (!search) return;
  const typeFilter = document.querySelector('#client-type');
  const orden = document.querySelector('#orden');
  const rows = [...document.querySelectorAll('.case-row')];
  const cuenta = document.getElementById('n-visibles');
  const sinResultados = document.getElementById('no-results');
  const textoInicial = cuenta ? cuenta.textContent : '';
  const buscado = search.value.trim().toLocaleLowerCase('es');
  const hayMas = Boolean(document.querySelector('.ver-mas'));
  function filterRows() {
    const term = search.value.trim().toLocaleLowerCase('es');
    const afinar = term !== buscado;                 // lo que ya busca el servidor no se vuelve a filtrar
    let visible = 0;
    rows.forEach(row => {
      const show = !afinar || row.dataset.search.includes(term);
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (rows.length) sinResultados.hidden = visible !== 0;
    if (cuenta) {
      let texto = textoInicial;
      if (afinar) {
        texto = visible === 0 ? 'Ningún caso coincide' : visible + (visible === 1 ? ' caso a la vista' : ' casos a la vista');
        if (hayMas) texto += ' entre los que se muestran. Pulsa Buscar para mirar todos.';
      }
      if (cuenta.textContent !== texto) cuenta.textContent = texto;
    }
    document.querySelectorAll('.grupo').forEach(grupo => {
      const coinciden = [...grupo.querySelectorAll('.case-row')].filter(row => !row.hidden).length;
      grupo.hidden = coinciden === 0;
      if (afinar) abrirGrupo(grupo, true);           // al afinar se ven los correos que coinciden, no solo el primero
    });
  }
  function abrirGrupo(grupo, abrir) {
    const boton = grupo.querySelector('.grupo-toggle');
    grupo.querySelector('.grupo-resto').hidden = !abrir;
    boton.setAttribute('aria-expanded', String(abrir));
    boton.textContent = abrir ? 'Ocultar' : 'Ver los ' + boton.dataset.n;
  }
  document.querySelectorAll('.grupo-toggle').forEach(boton => boton.addEventListener('click', () => {
    abrirGrupo(boton.closest('.grupo'), boton.getAttribute('aria-expanded') !== 'true');
  }));
  search.addEventListener('input', filterRows);
  // Cambiar el orden o el tipo pide la lista de nuevo al servidor (con la etapa que ya estaba elegida).
  const buscar = search.form && search.form.querySelector('button[type="submit"]:not(.chip-filter)');
  [orden, typeFilter].forEach(campo => campo && campo.addEventListener('change', () => {
    if (campo.form.requestSubmit) campo.form.requestSubmit(buscar); else campo.form.submit();
  }));
  const checks = [...document.querySelectorAll('.row-check')];
  const selectAll = document.querySelector('#select-all');
  const deleteBtn = document.querySelector('#delete-btn');
  function updateSelection() {
    const picked = checks.filter(c => c.checked).length;
    document.querySelector('#selected-count').textContent = picked + (picked === 1 ? ' marcada' : ' marcadas');
    deleteBtn.disabled = picked === 0;
    deleteBtn.textContent = picked ? 'Borrar ' + picked + (picked === 1 ? ' caso marcado' : ' casos marcados') : 'Borrar las marcadas';
    // Un caso con código T o correo enviado pide escribir ELIMINAR (el servidor también lo exige; U-57).
    const fuerte = document.querySelector('#eliminar-fuerte');
    if (fuerte) {
      const protegidos = checks.some(c => c.checked && c.hasAttribute('data-protegido'));
      fuerte.hidden = !protegidos;
      if (!protegidos) fuerte.querySelector('input').value = '';
    }
  }
  selectAll.addEventListener('change', () => {
    checks.forEach(c => { if (!c.closest('.case-row').hidden) c.checked = selectAll.checked; });
    updateSelection();
  });
  checks.forEach(c => c.addEventListener('change', updateSelection));
  updateSelection();
  const linkChecks = [...document.querySelectorAll('.link-row-check')];
  const selectAllLinks = document.querySelector('#select-all-links');
  const dismissLinks = document.querySelector('#dismiss-links-btn');
  function updateLinkSelection() {
    if (!dismissLinks) return;
    const picked = linkChecks.filter(c => c.checked).length;
    document.querySelector('#links-selected-count').textContent = picked + (picked === 1 ? ' marcada' : ' marcadas');
    dismissLinks.disabled = picked === 0;
  }
  if (selectAllLinks) selectAllLinks.addEventListener('change', () => {
    linkChecks.forEach(c => { c.checked = selectAllLinks.checked; });
    updateLinkSelection();
  });
  linkChecks.forEach(c => c.addEventListener('change', updateLinkSelection));
  // una casilla oculta por la búsqueda no debe borrarse sin que se vea
  function clearHiddenSelection() {
    checks.forEach(c => { if (c.closest('.case-row').hidden) c.checked = false; });
    selectAll.checked = false;
    updateSelection();
  }
  search.addEventListener('input', clearHiddenSelection);
})();
document.querySelectorAll('form[data-datos-comerciales]').forEach(formulario => {
  // Dejar vacío un dato que ya tenía algo pide confirmar (U-57). El botón lleva `data-confirm` solo en ese caso.
  const boton = formulario.querySelector('button[type="submit"]');
  if (!boton) return;
  boton.addEventListener('click', () => {
    const vaciados = [...formulario.querySelectorAll('[data-nombre]')]
      .filter(campo => campo.defaultValue.trim() && !campo.value.trim())
      .map(campo => '«' + campo.dataset.nombre + '» (ahora dice «' + campo.defaultValue.trim().slice(0, 60) + '»)');
    if (vaciados.length) boton.dataset.confirm = 'Vas a dejar vacío ' + vaciados.join(' y ') + '. ¿Seguimos?';
    else delete boton.dataset.confirm;
  });
});
document.querySelectorAll('[data-banner-vinculos]').forEach(root => {
  // «Posibles actualizaciones» del caso: seleccionar todas, y no enviar sin marcar ninguna.
  const toggle = root.querySelector('[data-select-all-links]');
  if (!toggle) return;
  const sync = () => root.querySelectorAll('[data-link-checkbox]').forEach(hidden => {
    const visible = root.querySelector(`[data-link-id="${hidden.value}"]`);
    hidden.checked = Boolean(visible && visible.checked);
  });
  root.querySelectorAll('[data-link-id]').forEach(box => box.addEventListener('change', sync));
  toggle.addEventListener('change', () => {
    root.querySelectorAll('[data-link-id]').forEach(box => { box.checked = toggle.checked; });
    sync();
  });
  root.querySelector('.link-bulk-form').addEventListener('submit', event => {
    sync();
    root.querySelector('[data-links-error]').hidden = true;
    if (!root.querySelector('[data-link-checkbox]:checked')) {
      event.preventDefault();
      const aviso = root.querySelector('[data-links-error]');
      aviso.hidden = false;
      const primera = root.querySelector('[data-link-id]');
      if (primera) primera.focus();
    }
  });
});
(function () {
  // Pestañas del caso (U-31): con JavaScript se muestra un panel a la vez; sin él, todos se ven uno tras otro.
  document.querySelectorAll('[data-tabs]').forEach(raiz => {
    const pestanas = Array.from(raiz.querySelectorAll('[role="tab"]'));
    const panel = pestana => document.getElementById(pestana.getAttribute('aria-controls'));
    function mostrar(elegida) {
      pestanas.forEach(p => {
        const activa = p === elegida;
        p.setAttribute('aria-selected', String(activa));
        p.tabIndex = activa ? 0 : -1;
        const destino = panel(p);
        if (destino) destino.hidden = !activa;
      });
    }
    function porEnlace() {   // un enlace a algo que está dentro de una pestaña la abre
      const destino = location.hash && document.getElementById(location.hash.slice(1));
      const contenedor = destino && destino.closest('[role="tabpanel"]');
      const pestana = contenedor && pestanas.find(p => p.getAttribute('aria-controls') === contenedor.id);
      if (pestana) { mostrar(pestana); destino.scrollIntoView(); }   // el panel estaba oculto: el navegador no pudo llevarte hasta él
    }
    pestanas.forEach(p => {
      p.addEventListener('click', () => mostrar(p));
      p.addEventListener('keydown', evento => {
        const paso = evento.key === 'ArrowRight' ? 1 : evento.key === 'ArrowLeft' ? -1 : 0;
        if (!paso && evento.key !== 'Home' && evento.key !== 'End') return;
        evento.preventDefault();
        const siguiente = evento.key === 'Home' ? pestanas[0] : evento.key === 'End' ? pestanas[pestanas.length - 1]
          : pestanas[(pestanas.indexOf(p) + paso + pestanas.length) % pestanas.length];
        siguiente.focus();
        mostrar(siguiente);
      });
    });
    mostrar(pestanas.find(p => p.getAttribute('aria-selected') === 'true') || pestanas[0]);
    porEnlace();
    window.addEventListener('hashchange', porEnlace);
  });
})();
(function () {
  // El aviso de siguiente paso se pega justo debajo del encabezado fijo, sin taparlo.
  const encabezado = document.querySelector('header');
  function medirEncabezado() {
    const fijo = getComputedStyle(encabezado).position === 'sticky';
    document.documentElement.style.setProperty('--header-h', (fijo ? encabezado.offsetHeight : 0) + 'px');
  }
  medirEncabezado();
  window.addEventListener('resize', medirEncabezado);
  // Un enlace a una sección plegada (#respuesta) la abre.
  function abrirDestino() {
    const destino = location.hash && document.getElementById(location.hash.slice(1));
    if (destino && destino.tagName === 'DETAILS') destino.open = true;
  }
  abrirDestino();
  window.addEventListener('hashchange', abrirDestino);
  // Un campo obligatorio vacío dentro de una sección plegada la abre: así el aviso del navegador se ve (U-40).
  document.addEventListener('invalid', evento => {
    let hijo = evento.target.closest ? evento.target.closest('details') : null;
    while (hijo) { hijo.open = true; hijo = hijo.parentElement ? hijo.parentElement.closest('details') : null; }
  }, true);
})();
(function () {
  // Tabla de actividades: al arrastrar la esquina de una caja, todas las de su fila toman esa altura (la fila sigue pareja)
  // y la caja se desplaza por dentro si el texto no cabe. Sin arrastrar, las cajas se ajustan solas al texto.
  const tabla = document.querySelector('.act-table');
  if (!tabla) return;
  // El navegador no avisa del arrastre de la esquina con eventos del ratón, pero sí escribe la altura en el estilo de la caja.
  let puestas = new WeakMap();                        // altura que puso este script, para no reaccionar a ella
  const observador = new MutationObserver(cambios => {
    for (const cambio of cambios) {
      const caja = cambio.target;
      const alto = caja.style.height;
      if (!alto || puestas.get(caja) === alto) continue;
      caja.closest('.act-main, .act-grid').querySelectorAll('textarea, select').forEach(c => {
        if (c === caja) return;
        puestas.set(c, alto);
        c.style.height = alto;
      });
    }
  });
  tabla.querySelectorAll('textarea').forEach(c => observador.observe(c, {attributes: true, attributeFilter: ['style']}));
})();
(function () {
  // Barra de guardado de la tabla de actividades (U-21): dice qué cambió, pide el motivo solo si hay cambios y
  // confirma cuántas filas se quitan. Sin este script el formulario sigue funcionando (todo visible y activo).
  const barra = document.getElementById('barra-guardado');
  if (!barra) return;
  const formulario = barra.closest('form');
  const guardar = document.getElementById('guardar-tabla');
  const estado = document.getElementById('estado-cambios');
  const motivo = document.getElementById('caja-motivo');
  const controles = [...formulario.querySelectorAll('.act-table textarea, .act-table select, .act-table input[type=checkbox]')];
  const valorDe = c => c.type === 'checkbox' ? c.checked : c.value;
  const inicial = new Map(controles.map(c => [c, valorDe(c)]));
  const plural = (n, uno, varios) => n + ' ' + (n === 1 ? uno : varios);
  function revisar() {
    const editadas = new Set(), quitadas = new Set(), nuevas = new Set();
    controles.forEach(c => {
      const fila = c.closest('.act-row');
      // Una fila existente con todas las celdas vaciadas se guarda como quitada: se cuenta y se confirma como tal (U-34.5).
      const vaciada = !fila.querySelector('.act-main.nueva') &&
        Array.from(fila.querySelectorAll('textarea')).every(t => !t.value.trim());
      if (c.type === 'checkbox') {
        fila.classList.toggle('por-quitar', c.checked || vaciada);
        if (c.checked || vaciada) quitadas.add(fila);
      } else if (vaciada) {
        quitadas.add(fila);
      } else if (valorDe(c) !== inicial.get(c)) {
        if (fila.querySelector('.act-main.nueva')) { if (c.tagName !== 'SELECT') nuevas.add(fila); }  // solo el estado no cuenta
        else editadas.add(fila);
      }
    });
    quitadas.forEach(fila => editadas.delete(fila));
    const partes = [];
    if (editadas.size) partes.push(plural(editadas.size, 'fila editada', 'filas editadas'));
    if (quitadas.size) partes.push(quitadas.size + ' por quitar');
    if (nuevas.size) partes.push(plural(nuevas.size, 'nueva', 'nuevas'));
    const hay = partes.length > 0;
    const textoEstado = hay ? 'Cambios sin guardar: ' + partes.join(', ') + '.' : estado.dataset.sinCambios;
    if (estado.textContent !== textoEstado) estado.textContent = textoEstado;
    barra.classList.toggle('con-cambios', hay);
    guardar.disabled = !hay;
    motivo.hidden = !hay;
    if (quitadas.size) guardar.dataset.confirm = (quitadas.size === 1 ? 'Se quitará ' : 'Se quitarán ') +
      plural(quitadas.size, 'actividad', 'actividades') + ' de la tabla (queda en el historial). ¿Confirmas?';
    else delete guardar.dataset.confirm;
  }
  formulario.addEventListener('input', revisar);
  formulario.addEventListener('change', revisar);
  revisar();
})();
(function () {
  // Tema: sistema → claro → oscuro; se recuerda en este navegador.
  const nombres = {system: 'sistema', light: 'claro', dark: 'oscuro'};
  const orden = ['system', 'light', 'dark'];
  const boton = document.getElementById('theme-toggle');
  let actual = 'system';
  try { actual = localStorage.getItem('anka-tema') || 'system'; } catch (e) { /* sin almacenamiento */ }
  if (!nombres[actual]) actual = 'system';
  function aplicar() {
    if (actual === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = actual;
    boton.textContent = 'Tema: ' + nombres[actual];
    // Dice el tema actual y a cuál cambia al pulsar (U-80).
    const siguiente = orden[(orden.indexOf(actual) + 1) % orden.length];
    // El nombre accesible empieza por el texto visible («Tema: claro»): quien manda por voz dice lo que ve (U-37).
    const ayuda = 'Tema: ' + nombres[actual] + '. Pulsa para usar ' + (siguiente === 'system' ? 'el del sistema' : 'el ' + nombres[siguiente]);
    boton.setAttribute('aria-label', ayuda);
    boton.title = ayuda;
  }
  boton.addEventListener('click', () => {
    actual = orden[(orden.indexOf(actual) + 1) % orden.length];
    try { if (actual === 'system') localStorage.removeItem('anka-tema'); else localStorage.setItem('anka-tema', actual); } catch (e) { /* sin almacenamiento */ }
    aplicar();
  });
  aplicar();
})();
(function () {
  let base = null, dirty = false;
  const chip = document.getElementById('bridge-chip');
  const googleChip = document.getElementById('google-chip');
  const googleForm = document.getElementById('google-reauth');
  const googleStatus = document.getElementById('google-status');
  const botonAvisos = document.getElementById('avisos-navegador');
  // El aviso «Google desconectado» lleva al único botón «Volver a conectar Google», el de Ajustes (U-80).
  googleChip.addEventListener('click', event => {
    event.preventDefault();
    document.getElementById('ajustes').open = true;
    googleForm.querySelector('button').focus();
  });
  const tituloBase = document.title.replace(/^\(\d+\) /, '');
  let sinLeer = null;
  function mostrarConteo(n) {
    document.title = (n ? '(' + n + ') ' : '') + tituloBase;
    const cuenta = document.querySelector('.brand .nav-count');
    if (cuenta) { cuenta.textContent = n; cuenta.hidden = !n; cuenta.title = n + ' con novedades por revisar'; }
  }
  function avisarDelNavegador(n) {
    // Solo con la pestaña oculta y si la persona lo activó; no sale nada fuera de este navegador.
    try {
      if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        new Notification('Anka', {body: n + (n === 1 ? ' caso con novedades por revisar' : ' casos con novedades por revisar')});
      }
    } catch (e) { /* sin permiso o sin soporte: queda el título */ }
  }
  try {
    if (botonAvisos && 'Notification' in window && Notification.permission === 'default') {
      botonAvisos.hidden = false;
      botonAvisos.addEventListener('click', () => {
        Notification.requestPermission().then(() => { botonAvisos.hidden = true; });
      });
    }
  } catch (e) { /* sin soporte: no se ofrece */ }
  // La página de un caso, ya a la vista, marca como leídas sus novedades (solo las de ese caso).
  const formVisto = document.getElementById('visto');
  function marcarVisto() {
    if (!formVisto || document.visibilityState !== 'visible') return;
    fetch(formVisto.action, {method: 'POST', body: new URLSearchParams(new FormData(formVisto))})
      .then(() => { base = null; poll(); }).catch(() => {});
  }
  document.addEventListener('visibilitychange', marcarVisto);
  document.addEventListener('input', () => { dirty = true; });
  document.getElementById('fresh-reload').addEventListener('click', e => { e.preventDefault(); location.reload(); });
  // Recargar por sí sola borraría lo que la persona está haciendo (U-34.3): solo se recarga si no está leyendo
  // más abajo, no escribió nada, no tiene el cursor en un campo y no hay un diálogo abierto. Si no, avisa.
  function puedeRecargar() {
    if (dirty) return false;
    if (window.scrollY > 0) return false;
    const dialogo = document.getElementById('confirmar');
    if (dialogo && dialogo.open) return false;
    const activo = document.activeElement;
    if (activo && /^(INPUT|TEXTAREA|SELECT)$/.test(activo.tagName)) return false;
    return !Array.from(document.querySelectorAll('textarea, input:not([type=hidden]):not([type=checkbox]):not([type=radio])'))
      .some(campo => campo.value !== campo.defaultValue);
  }
  // El estado de la revisión de correos y de Google cambia en silencio: se dice en voz alta solo cuando cambia (U-37).
  const anuncio = document.getElementById('anuncio-estado');
  let anunciado = null;
  function anunciar(partes) {
    const texto = partes.filter(Boolean).join(' ');
    if (anunciado === null) { anunciado = texto; return; }       // el primer estado es el de la página, no una novedad
    if (texto !== anunciado) { anunciado = texto; anuncio.textContent = texto; }
  }
  async function poll() {
    try {
      const r = await fetch('/state', {cache: 'no-store'});
      if (!r.ok) return;
      const s = await r.json();
      if (s.bridge) {
        chip.hidden = false;
        chip.dataset.state = s.bridge;
        chip.title = s.text;
        chip.textContent = {ok: 'Revisión de correos: activa', attention: 'Revisión de correos: hay un análisis por revisar',
                            error: 'Revisión de correos: con avisos', stopped: 'Revisión de correos: detenida',
                            never: 'Revisión de correos: sin iniciar'}[s.bridge];
      } else { chip.hidden = true; }
      const g = s.google || {};
      const needsGoogle = g.estado === 'vencida' || g.estado === 'fallida';
      googleForm.classList.toggle('urgente', needsGoogle);
      googleChip.hidden = !(needsGoogle || g.estado === 'en_curso');
      googleChip.dataset.state = needsGoogle ? 'stopped' : 'error';
      googleChip.title = g.texto || '';
      if (g.texto) googleStatus.textContent = g.texto;
      googleChip.textContent = needsGoogle ? 'Google desconectado · Volver a conectar' : 'Conectando con Google…';
      anunciar([chip.hidden ? '' : chip.textContent + (s.text ? '. ' + s.text : ''), googleChip.hidden ? '' : googleChip.textContent + (g.texto ? '. ' + g.texto : '')]);
      if (typeof s.sin_leer === 'number') {
        if (sinLeer !== null && s.sin_leer > sinLeer) avisarDelNavegador(s.sin_leer);
        sinLeer = s.sin_leer;
        mostrarConteo(s.sin_leer);
      }
      if (base === null) base = s.v;
      else if (s.v !== base) {
        if (puedeRecargar()) location.reload();
        else document.getElementById('fresh-banner').hidden = false;
      }
    } catch (e) { /* servidor local cerrado: se reintenta */ }
  }
  poll();
  marcarVisto();
  setInterval(poll, 20000);
  // Aviso inmediato del servidor (U-23): si el navegador no lo admite o se corta, queda la consulta de arriba.
  if (window.EventSource) {
    const avisos = new EventSource('/eventos');
    avisos.onmessage = () => poll();
  }
})();

// El tiempo de espera avanza sin recargar la página ni interrumpir lo que se está leyendo.
(() => {
  const filas = document.querySelectorAll('[data-recepcion-avance]');
  if (!filas.length) return;
  function actualizar() {
    filas.forEach(fila => {
      const fecha = Date.parse(fila.dataset.recepcionAvance);
      if (!Number.isFinite(fecha)) return;
      const minutos = Math.max(0, Math.floor((Date.now() - fecha) / 60000));
      const horas = Math.floor(minutos / 60), resto = minutos % 60;
      fila.querySelector('[data-tiempo-avance]').textContent = minutos < 1 ? 'Hace menos de un minuto' :
        minutos < 60 ? `Hace ${minutos} min` : `Hace ${horas} h${resto ? ` ${resto} min` : ''}`;
      const demora = fila.dataset.esperaWork === 'true' && minutos >= Number(fila.dataset.alertaMin);
      fila.querySelector('[data-demora-avance]').hidden = !demora;
      if (demora) fila.querySelector('[data-siguiente-avance]').textContent =
        'Revisar la tarea de análisis: aún no ha entregado un resultado.';
    });
  }
  actualizar();
  setInterval(actualizar, 60000);
})();
(function () {
  // «☰ Casos»: en pantallas angostas la barra de casos se abre y se cierra; sin JavaScript queda a la vista (U-112.A.1).
  const barra = document.getElementById('barra-casos'), boton = document.getElementById('abrir-barra');
  if (!barra || !boton) return;
  barra.classList.add('plegable');
  boton.hidden = false;
  boton.addEventListener('click', () => {
    const abrir = !barra.classList.contains('abierta');
    barra.classList.toggle('abierta', abrir);
    boton.setAttribute('aria-expanded', String(abrir));
    if (abrir) barra.querySelector('a').focus();
  });
})();
(function () {
  // U-113: el correo con las preguntas se escribe solo mientras se marcan «Preguntar» y se puede cambiar a mano.
  // Con el borrador ya redactado («Correo por revisar») la misma casilla agrega o quita su línea del correo (U-112, etapa 7).
  const caja = document.getElementById('correo-unico') || document.getElementById('cuerpo-correo');
  if (!caja) return;
  const soloLineas = caja.id === 'cuerpo-correo';
  if (soloLineas && !document.querySelector('input[name="preguntar_fila"]')) return;
  const nota = document.getElementById('aviso-espera');
  const botones = ['btn-enviar-preguntas', 'btn-borrador-preguntas'].map(id => document.getElementById(id)).filter(Boolean);
  const casillas = () => Array.from(document.querySelectorAll('input[name="preguntar_fila"]'));
  const marcadas = () => casillas().filter(c => c.checked).map(c => c.value.trim()).filter(Boolean);
  const plano = t => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const NUMERADA = /^\s*\d+\s*[.)]\s+(.*)$/;
  const textoDe = linea => linea.match(NUMERADA)[1];
  let generado = soloLineas ? null : caja.value;      // lo último que escribió el programa: si el texto sigue igual, nadie lo cambió a mano
  let cola = Promise.resolve();   // una casilla a la vez, en el orden en que se marcaron

  async function pedir(lista, todas = []) {   // el servidor arma el texto con la misma plantilla que el envío (sin modelo ni costo)
    const consulta = new URLSearchParams({thread_id: caja.dataset.caso});
    lista.forEach(p => consulta.append('p', p));
    todas.forEach(p => consulta.append('t', p));
    const respuesta = await fetch('/api/correo-preguntas?' + consulta.toString(), {headers: {Accept: 'application/json'}});
    if (!respuesta.ok) throw new Error('correo');
    return respuesta.json();
  }
  const renumerar = lineas => { let n = 0; return lineas.map(l => NUMERADA.test(l) ? (++n) + '. ' + textoDe(l) : l); };

  async function poner(casilla) {
    if (!casilla.value.trim()) return;
    if (caja.value === generado) {                      // sin cambios a mano: se rehace entero
      const dato = await pedir(marcadas());
      generado = caja.value = dato.cuerpo;
      return;
    }
    // Con cambios a mano (U-114): se reemplaza solo lo que escribió el programa (preguntas sueltas y líneas que reúnen
    // varias) y se conserva lo demás. Lo repetido se pide una sola vez, en el orden en que se marquen las casillas.
    const dato = await pedir(marcadas(), casillas().map(c => c.value.trim()).filter(Boolean));
    const conocidas = new Set(dato.conocidas.map(plano));
    const delPrograma = l => NUMERADA.test(l) && (conocidas.has(plano(textoDe(l)))
      || dato.grupos.some(g => plano(textoDe(l)).startsWith(plano(g))));
    const resto = [];
    let donde = -1;
    caja.value.split('\n').forEach(l => { if (delPrograma(l)) { if (donde < 0) donde = resto.length; } else resto.push(l); });
    const escritas = new Set(resto.filter(l => NUMERADA.test(l)).map(l => plano(textoDe(l))));
    const nuevas = dato.lineas.filter(l => !escritas.has(plano(l))).map(l => '0. ' + l);
    if (donde < 0) {
      const ultima = resto.reduce((u, l, k) => NUMERADA.test(l) ? k : u, -1);
      if (ultima < 0) { if (nuevas.length) caja.value = dato.cuerpo; return; }
      donde = ultima + 1;
    }
    resto.splice(donde, 0, ...nuevas);
    caja.value = renumerar(resto).join('\n');
  }

  function estado() {
    const n = marcadas().length;
    botones.forEach(b => { b.disabled = n === 0; });
    if (nota) nota.textContent = n === 0 ? 'Marca «Preguntar» en lo que falta y el correo se escribe solo.'
      : n + (n === 1 ? ' pregunta' : ' preguntas') + ' en el correo.';
  }
  casillas().forEach(c => c.addEventListener('change', () => {
    estado();
    cola = cola.then(() => poner(c)).catch(() => {
      if (nota) nota.textContent = 'No se pudo actualizar el correo. Vuelve a marcar la casilla o escríbelo tú.';
    });
  }));
  if (soloLineas) return;
  let enviando = false;
  document.addEventListener('submit', e => { if (e.target.method && e.target.method.toLowerCase() !== 'dialog') enviando = true; });
  window.addEventListener('beforeunload', e => { if (!enviando && caja.value !== caja.defaultValue) { e.preventDefault(); e.returnValue = ''; } });
})();
