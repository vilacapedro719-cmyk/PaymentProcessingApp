const authView = document.querySelector('#auth-view');
const appView = document.querySelector('#app-view');
const authForm = document.querySelector('#auth-form');
const transferForm = document.querySelector('#transfer-form');
let mode = 'login';
let account = null;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Something went wrong. Please try again.');
  return data;
}

function setMessage(node, text, success = false) {
  node.textContent = text;
  node.classList.toggle('success', success);
}

function setMode(next) {
  mode = next;
  document.querySelector('#auth-title').textContent = mode === 'login' ? 'Welcome back' : 'Create your account';
  document.querySelector('#auth-description').textContent = mode === 'login' ? 'Sign in to your account to continue.' : 'A new account is ready in just a moment.';
  document.querySelector('#auth-submit').textContent = mode === 'login' ? 'Log in' : 'Create account';
  document.querySelector('#switch-prompt').textContent = mode === 'login' ? 'New here?' : 'Already have an account?';
  document.querySelector('#switch-mode').textContent = mode === 'login' ? 'Create an account' : 'Log in';
  document.querySelector('#auth-password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  const email = document.querySelector('#auth-email');
  document.querySelector('#email-field').hidden = mode === 'login';
  email.required = mode === 'register';
  setMessage(document.querySelector('#auth-message'), '');
}

document.querySelector('#switch-mode').addEventListener('click', () => setMode(mode === 'login' ? 'register' : 'login'));

authForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = document.querySelector('#auth-message');
  const button = document.querySelector('#auth-submit');
  button.disabled = true;
  setMessage(message, '');
  try {
    const credentials = { name: document.querySelector('#auth-name').value.trim(), password: document.querySelector('#auth-password').value };
    if (mode === 'register') credentials.email = document.querySelector('#auth-email').value.trim();
    account = await api(`/auth/${mode}`, { method: 'POST', body: JSON.stringify(credentials) });
    await showDashboard();
  } catch (error) { setMessage(message, error.message); }
  finally { button.disabled = false; }
});

async function showDashboard() {
  authView.hidden = true;
  appView.hidden = false;
  document.querySelector('#user-name').textContent = account.name;
  document.querySelector('#balance').textContent = money(account.balance_cents);
  await loadDashboard();
}

async function loadDashboard() {
  const [freshAccount, transfers] = await Promise.all([
    api(`/accounts/${account.account_id}`),
    api(`/accounts/${account.account_id}/transfers`),
  ]);
  account = freshAccount;
  document.querySelector('#balance').textContent = money(account.balance_cents);
  const history = document.querySelector('#history');
  history.replaceChildren();
  if (!transfers.length) { history.innerHTML = '<p class="empty">Your transfers will show up here.</p>'; return; }
  for (const transfer of transfers) {
    const deposit = transfer.kind === 'deposit';
    const sent = transfer.sender_id === account.account_id && !deposit;
    const row = document.createElement('div'); row.className = 'history-item';
    const details = document.createElement('div');
    const who = document.createElement('div'); who.className = 'transfer-who'; who.textContent = deposit ? 'Initial deposit' : `${sent ? 'To' : 'From'} ${transfer.counterparty_name || 'Account'}`;
    const meta = document.createElement('div'); meta.className = 'transfer-meta';
    details.append(who);
    if (deposit) {
      meta.textContent = 'Opening balance';
    } else {
      const email = document.createElement('div'); email.className = 'transfer-email'; email.textContent = transfer.counterparty_email || '';
      details.append(email);
      meta.textContent = new Date(`${transfer.created_at.replace(' ', 'T')}Z`).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }
    details.append(meta);
    const amount = document.createElement('div'); amount.className = `transfer-amount ${sent ? 'sent' : 'received'}`; amount.textContent = `${sent ? '−' : '+'}${money(transfer.amount_cents)}`;
    row.append(details, amount); history.append(row);
  }
}

function money(cents) { return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'EUR' }).format(cents / 100); }

transferForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = transferForm.querySelector('button');
  const message = document.querySelector('#transfer-message');
  const amountCents = Math.round(Number(document.querySelector('#amount').value) * 100);
  const receiverEmail = document.querySelector('#recipient-email').value.trim();
  button.disabled = true;
  setMessage(message, '');
  try {
    await api(`/accounts/${account.account_id}/transfers`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ receiver_email: receiverEmail, amount_cents: amountCents }),
    });
    transferForm.reset();
    setMessage(message, 'Transfer sent successfully.', true);
    await loadDashboard();
  } catch (error) { setMessage(message, error.message); }
  finally { button.disabled = false; }
});

document.querySelector('#refresh').addEventListener('click', async () => {
  try { await loadDashboard(); } catch (error) { setMessage(document.querySelector('#transfer-message'), error.message); }
});

document.querySelector('#logout').addEventListener('click', () => {
  account = null;
  appView.hidden = true;
  authView.hidden = false;
  authForm.reset();
  setMode('login');
});
