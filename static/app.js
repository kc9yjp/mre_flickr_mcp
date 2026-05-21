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

function showConfirm(message, onOk) {
  const overlay = document.getElementById('confirm-modal');
  const ok = overlay.querySelector('#confirm-ok');
  const cancel = overlay.querySelector('#confirm-cancel');

  document.getElementById('confirm-msg').textContent = message;
  overlay.hidden = false;
  ok.focus();

  function teardown(confirmed) {
    overlay.hidden = true;
    ok.removeEventListener('click', handleOk);
    cancel.removeEventListener('click', handleCancel);
    overlay.removeEventListener('click', handleOverlay);
    document.removeEventListener('keydown', handleKey);
    if (confirmed) onOk();
  }

  function handleOk() { teardown(true); }
  function handleCancel() { teardown(false); }
  function handleOverlay(e) { if (e.target === overlay) teardown(false); }
  function handleKey(e) {
    if (e.key === 'Escape') teardown(false);
    else if (e.key === 'Enter') teardown(true);
  }

  ok.addEventListener('click', handleOk);
  cancel.addEventListener('click', handleCancel);
  overlay.addEventListener('click', handleOverlay);
  document.addEventListener('keydown', handleKey);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', e => {
      e.preventDefault();
      showConfirm(form.dataset.confirm, () => {
        form.removeAttribute('onsubmit');
        form.submit();
      });
    });
  });

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
