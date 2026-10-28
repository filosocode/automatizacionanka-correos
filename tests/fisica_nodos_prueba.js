// Prueba en Node de la física de los nodos de fondo (U-38, D-103). La lanza tests/test_particulas.py.
// Extrae el bloque «física» de app.js (el que se ejecuta en el navegador), lo compara con una transcripción
// aparte de la matemática de tsparticles 3.9.1 y escribe un JSON con las medidas.
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'anka', 'web', 'templates', 'app.js'), 'utf8');
const a = html.indexOf('// <física>'), b = html.indexOf('// </física>');
if (a < 0 || b < 0) throw new Error('falta el bloque de física');
const F = new Function(html.slice(a, b) + '\nreturn { NODOS, cuantos, nodoNuevo, ajustar, repulsar, rebotar, avanzar };')();

// --- transcripción de tsparticles 3.9.1 (index.js): mover base, repulsor externo y rebote
const ref = {
  // p = moveSpeed * (tamaño: 1) * (lento: 1) * delta.factor / 2 ; la posición suma velocity(unitaria) * p
  pasoMover: (speed, factor) => speed * 1 * 1 * factor / 2,
  // factor del bucle: Dy(ms, fpsLimit, smooth=false) = 60 * ms / 1000
  factor: ms => 60 * ms / 1000,
  // _processRepulse: distancia 100, factor 100, speed 1, maxSpeed 50, easeOutQuad
  empuje: d => Math.min(Math.max((1 - (1 - (1 - d / 100)) ** 2) * (1 * 100), 0), 50),
};

// azar determinista
let semilla = 12345;
const azar = () => { semilla = (semilla * 1664525 + 1013904223) % 4294967296; return semilla / 4294967296; };

const salida = {};

// 1) velocidad: un nodo libre recorre 18 px/s (0.6 * 60 / 2) con 60, 30 y 144 cuadros por segundo
function recorrido(fps, segundos) {
  const n = { x: 5000, y: 5000, vx: 1, vy: 0, r: 2, color: 0 };
  const lista = [n];
  const ms = 1000 / fps;
  for (let i = 0; i < Math.round(fps * segundos); i++) F.avanzar(lista, 1e5, 1e5, ref.factor(ms), null);
  return n.x - 5000;
}
salida.px_por_segundo = { f60: recorrido(60, 2) / 2, f30: recorrido(30, 2) / 2, f144: recorrido(144, 2) / 2,
                          esperado: ref.pasoMover(0.6, 1) * 60 };

// 2) un nodo aleatorio conserva rapidez unitaria y se queda dentro tras 60 s de rebotes
let fuera = 0, rapidezMin = 9, rapidezMax = 0;
{
  const lista = [];
  for (let i = 0; i < 40; i++) lista.push(F.nodoNuevo(800, 500, azar));
  for (let i = 0; i < 3600; i++) {
    F.avanzar(lista, 800, 500, 1, null);
    for (const n of lista) {
      if (n.x - n.r < -1e-9 || n.x + n.r > 800 + 1e-9 || n.y - n.r < -1e-9 || n.y + n.r > 500 + 1e-9) fuera++;
      const v = Math.hypot(n.vx, n.vy); rapidezMin = Math.min(rapidezMin, v); rapidezMax = Math.max(rapidezMax, v);
    }
  }
}
salida.rebote = { fuera, rapidezMin, rapidezMax };

// 3) empuje de un solo cuadro a distintas distancias, comparado con la transcripción
salida.empuje = [0.5, 10, 30, 50, 70.7, 90, 99, 100, 100.5].map(d => {
  const n = { x: 400 + d, y: 400, vx: 0, vy: 0, r: 2, color: 0 };
  F.repulsar([n], { x: 400, y: 400 });
  return { d, mide: n.x - (400 + d), ref: d > 100 ? 0 : ref.empuje(d) };
});

// 4) con el ratón quieto, todo nodo que estaba dentro de los 100 px termina fuera (en el borde del radio)
{
  const lista = [];
  for (let i = 0; i < 12; i++) { const t = i / 12 * Math.PI * 2; lista.push({ x: 600 + Math.cos(t) * (10 + 7 * i), y: 400 + Math.sin(t) * (10 + 7 * i), vx: Math.cos(t + 1), vy: Math.sin(t + 1), r: 2, color: 0 }); }
  const raton = { x: 600, y: 400 };
  let dentroAntes = lista.filter(n => Math.hypot(n.x - 600, n.y - 400) < 100).length;
  let maxMov = 0;
  for (let i = 0; i < 600; i++) {
    const antes = lista.map(n => [n.x, n.y]);
    F.avanzar(lista, 1200, 800, 1, raton);
    lista.forEach((n, k) => { maxMov = Math.max(maxMov, Math.hypot(n.x - antes[k][0], n.y - antes[k][1])); });
  }
  const dist = lista.map(n => Math.hypot(n.x - 600, n.y - 400));
  salida.ratón_quieto = { dentroAntes, minDistanciaDespues: Math.min(...dist), maxDistanciaDespues: Math.max(...dist), maxMovimientoPorCuadro: maxMov };
}

// 5) cuántos nodos según la pantalla (densidad de tsparticles sobre 1920x1080)
salida.cantidad = { '1920x1080': F.cuantos(1920, 1080), '1568x662': F.cuantos(1568, 662), '3840x2160': F.cuantos(3840, 2160), '360x640': F.cuantos(360, 640) };

// 6) ajustar al cambiar de tamaño: crece, encoge y no se pasa
{
  const lista = [];
  F.ajustar(lista, 1920, 1080, azar); const a1 = lista.length;
  F.ajustar(lista, 800, 600, azar); const a2 = lista.length;
  F.ajustar(lista, 1920, 1080, azar); const a3 = lista.length;
  salida.ajustar = [a1, a2, a3];
}

// 7) un cuadro muy tardío: el factor que usa el bucle (con tope de 100 ms) mueve a lo sumo 5.4 px
salida.tope_dt = { px_maximos: ref.pasoMover(0.6, ref.factor(100)), sin_tope_5s: ref.pasoMover(0.6, ref.factor(5000)) };

process.stdout.write(JSON.stringify(salida));
