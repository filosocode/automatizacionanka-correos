// Primer pintado con el tema guardado en este navegador (sin esperar al resto de los scripts, para que no parpadee).
try { var tema = localStorage.getItem('anka-tema'); if (tema === 'light' || tema === 'dark') document.documentElement.dataset.theme = tema; } catch (e) { /* sin almacenamiento: sigue el sistema */ }
