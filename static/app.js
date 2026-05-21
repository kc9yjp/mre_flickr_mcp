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

function fmtRelative(epochSecs) {
  const diffSecs = epochSecs - Math.floor(Date.now() / 1000);
  if (diffSecs <= 0) return 'now';
  const h = Math.floor(diffSecs / 3600);
  const m = Math.floor((diffSecs % 3600) / 60);
  if (h > 0) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

function updateSyncNext() {
  document.querySelectorAll('[data-next]').forEach(el => {
    el.textContent = fmtRelative(parseInt(el.dataset.next));
  });
}

function applySyncData(data) {
  data.rows.forEach(row => {
    const tr = document.querySelector(`tr[data-sync-type="${row.type}"]`);
    if (!tr) return;

    const lastCell = tr.querySelector('.sync-last');
    if (lastCell) {
      if (row.last) {
        const ms = row.last * 1000;
        const fmt = new Date(ms).toLocaleString(undefined, {
          month: 'short', day: 'numeric', year: 'numeric',
          hour: 'numeric', minute: '2-digit'
        });
        lastCell.innerHTML = `<time data-ts="${row.last}">${fmt}</time>`;
      } else {
        lastCell.textContent = '—';
      }
    }

    const durCell = tr.querySelector('.sync-duration-cell');
    if (durCell) durCell.textContent = row.duration || '—';

    const nextCell = tr.querySelector('.sync-next');
    if (nextCell) {
      nextCell.innerHTML = row.next
        ? `<span data-next="${row.next}"></span>`
        : '—';
    }

    const statusCell = tr.querySelector('.sync-status');
    if (statusCell) {
      statusCell.innerHTML = row.running
        ? '<span class="sync-running-badge">Syncing…</span>'
        : '—';
    }
  });
  updateSyncNext();

  const buttons = document.querySelectorAll('.sync-form .btn');
  buttons.forEach(btn => { btn.disabled = data.running; });
}

function initSyncPolling() {
  const table = document.getElementById('sync-table');
  if (!table) return;
  updateSyncNext();

  const note = document.querySelector('.sync-refresh-note');

  function poll() {
    fetch('/sync/status.json')
      .then(r => r.json())
      .then(data => {
        applySyncData(data);
        if (note) note.textContent = data.running ? 'Updating every 5 seconds' : 'Updates every 30 seconds';
        setTimeout(poll, data.running ? 5000 : 30000);
      })
      .catch(() => { setTimeout(poll, 30000); });
  }

  poll();
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

  initSyncPolling();

  document.querySelectorAll('time[data-ts]').forEach(el => {
    const ms = parseInt(el.dataset.ts) * 1000;
    el.textContent = new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });
  });
});
