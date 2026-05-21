function copyCmd(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
  });
}

function copyEl(btn, id) {
  const text = document.getElementById(id).textContent;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
  });
}

function showTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector('[data-tab="' + name + '"]').classList.add('active');
}

function fmtElapsed(startSec) {
  const s = Math.floor(Date.now() / 1000) - startSec;
  if (s < 60) return s + 's';
  return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('time[data-ts]').forEach(el => {
    const ms = parseInt(el.dataset.ts) * 1000;
    el.textContent = new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });
  });

  const elapsed = document.querySelectorAll('[data-elapsed]');
  if (elapsed.length) {
    function tick() {
      elapsed.forEach(el => { el.textContent = fmtElapsed(parseInt(el.dataset.elapsed)); });
    }
    tick();
    setInterval(tick, 1000);
  }
});
