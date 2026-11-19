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
