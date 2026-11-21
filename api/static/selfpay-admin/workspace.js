document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('.sp-menu-toggle');
  const nav = document.getElementById('sp-admin-navigation');
  if (!button || !nav) return;
  const close = () => { button.setAttribute('aria-expanded', 'false'); document.body.classList.remove('sp-menu-open'); };
  button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('sp-menu-open', open);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') { close(); button.focus(); }
  });
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', close));
  matchMedia('(min-width: 961px)').addEventListener('change', close);
});

document.addEventListener('DOMContentLoaded', () => {
  const notifications = document.querySelector('.sp-notifications');
  if (!notifications) return;
  document.addEventListener('click', event => {
    if (!notifications.contains(event.target)) notifications.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && notifications.open) {
      notifications.open = false;
      notifications.querySelector('summary').focus();
    }
  });
});

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.classList.contains('sp-login')) return;
  const header = document.getElementById('header');
  if (!header) return;
  const updateHeaderHeight = () => document.documentElement.style.setProperty('--sp-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
  updateHeaderHeight();
  if ('ResizeObserver' in window) new ResizeObserver(updateHeaderHeight).observe(header);
  else window.addEventListener('resize', updateHeaderHeight);
});
